import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import YAML from "yaml";

import { SPA_OPENAPI_CONTRACTS } from "./contracts";

type OpenApiDoc = {
  paths: Record<string, Record<string, OpenApiOperation>>;
  components?: {
    schemas?: Record<string, OpenApiSchema>;
  };
};

type OpenApiSchema = {
  $ref?: string;
  allOf?: OpenApiSchema[];
  type?: string;
  items?: OpenApiSchema;
  properties?: Record<string, OpenApiSchema>;
};

type OpenApiOperation = {
  responses?: Record<
    string,
    {
      content?: {
        "application/json"?: {
          schema?: OpenApiSchema;
        };
      };
    }
  >;
};

type SourceRouteReference = {
  filePath: string;
  route: string;
};

function loadOpenApiDoc(): OpenApiDoc {
  const currentFile = fileURLToPath(import.meta.url);
  const specPath = path.resolve(path.dirname(currentFile), "../../../docs/openapi.yaml");
  const yaml = fs.readFileSync(specPath, "utf8");
  return YAML.parse(yaml) as OpenApiDoc;
}

function resolveSchema(schema: OpenApiSchema | undefined, doc: OpenApiDoc): OpenApiSchema | undefined {
  if (!schema || typeof schema !== "object") {
    return schema;
  }

  if (schema.$ref) {
    const pointer = String(schema.$ref).replace(/^#\//, "").split("/");
    let resolved: unknown = doc;
    for (const segment of pointer) {
      if (!resolved || typeof resolved !== "object") {
        resolved = undefined;
        break;
      }
      resolved = (resolved as Record<string, unknown>)[segment];
    }
    if (!resolved) {
      throw new Error(`Unresolvable OpenAPI reference: ${schema.$ref}`);
    }
    return resolveSchema(resolved as OpenApiSchema, doc);
  }

  if (schema.allOf?.length) {
    return schema.allOf.reduce<OpenApiSchema>(
      (merged, part) => {
        const resolvedPart = resolveSchema(part, doc);
        if (!resolvedPart) {
          return merged;
        }

        return {
          ...merged,
          ...resolvedPart,
          properties: {
            ...merged.properties,
            ...resolvedPart.properties,
          },
        };
      },
      { properties: {} },
    );
  }

  if (schema.items) {
    return {
      ...schema,
      items: resolveSchema(schema.items, doc),
    };
  }

  if (schema.properties) {
    return {
      ...schema,
      properties: Object.fromEntries(
        Object.entries(schema.properties).map(([key, value]) => [key, resolveSchema(value, doc) ?? value]),
      ),
    };
  }

  return schema;
}

function resolveSchemaPath(
  schema: OpenApiSchema | undefined,
  doc: OpenApiDoc,
  fieldPath: string,
): OpenApiSchema | undefined {
  let current = resolveSchema(schema, doc);
  for (const segment of fieldPath.split(".")) {
    current = resolveSchema(current?.properties?.[segment], doc);
  }
  return current;
}

function collectSpaRouteReferences(): SourceRouteReference[] {
  const currentFile = fileURLToPath(import.meta.url);
  const spaSrcRoot = path.resolve(path.dirname(currentFile), "..");
  const targetFiles = [
    path.join(spaSrcRoot, "api", "client.ts"),
    path.join(spaSrcRoot, "hooks", "useJobPolling.ts"),
    ...walkFiles(path.join(spaSrcRoot, "pages")),
  ];

  const routePattern = /(["'`])((?:\/v1\/)[^"'`\n]+)\1/g;
  const references: SourceRouteReference[] = [];

  for (const filePath of targetFiles) {
    if (filePath.endsWith(".test.ts") || filePath.endsWith(".test.tsx")) {
      continue;
    }

    const source = fs.readFileSync(filePath, "utf8");
    for (const match of source.matchAll(routePattern)) {
      references.push({
        filePath,
        route: normalizeRoutePath(match[2]),
      });
    }
  }

  return references;
}

function walkFiles(root: string): string[] {
  const entries = fs.readdirSync(root, { withFileTypes: true });
  const files: string[] = [];

  for (const entry of entries) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkFiles(fullPath));
      continue;
    }
    if (entry.isFile()) {
      files.push(fullPath);
    }
  }

  return files;
}

function normalizeRoutePath(route: string): string {
  return route
    .split("?")[0]
    .replace(/\$\{\s*agentName\s*\}/g, "{agentName}")
    .replace(/\$\{\s*tenantId\s*\}/g, "{tenantId}")
    .replace(/\$\{\s*jobId\s*\}/g, "{jobId}")
    .replace(/\$\{\s*webhookId\s*\}/g, "{webhookId}");
}

describe("SPA/OpenAPI contract drift", () => {
  it("keeps SPA-consumed endpoints and response fields aligned", () => {
    const doc = loadOpenApiDoc();

    for (const contract of SPA_OPENAPI_CONTRACTS) {
      const pathItem = doc.paths?.[contract.path];
      expect(pathItem, `${contract.name}: missing path ${contract.path}`).toBeTruthy();

      const operation = pathItem?.[contract.method];
      expect(operation, `${contract.name}: missing method ${contract.method}`).toBeTruthy();

      const response = operation?.responses?.[contract.statusCode];
      expect(response, `${contract.name}: missing ${contract.statusCode} response`).toBeTruthy();

      const responseSchema = response?.content?.["application/json"]?.schema;
      expect(responseSchema, `${contract.name}: missing application/json schema`).toBeTruthy();

      if (contract.collectionProperty) {
        const resolvedRoot = resolveSchema(responseSchema, doc);
        const collectionSchema = resolveSchema(
          resolvedRoot?.properties?.[contract.collectionProperty],
          doc,
        );
        expect(
          collectionSchema,
          `${contract.name}: missing collection property ${contract.collectionProperty}`,
        ).toBeTruthy();

        const itemSchema =
          collectionSchema?.type === "array"
            ? resolveSchema(collectionSchema.items, doc)
            : collectionSchema;
        for (const fieldPath of contract.requiredFieldPaths) {
          expect(
            resolveSchemaPath(itemSchema, doc, fieldPath),
            `${contract.name}: missing field ${fieldPath} on collection item schema`,
          ).toBeTruthy();
        }
        continue;
      }

      const resolvedRoot = resolveSchema(responseSchema, doc);
      for (const fieldPath of contract.requiredFieldPaths) {
        expect(
          resolveSchemaPath(resolvedRoot, doc, fieldPath),
          `${contract.name}: missing field ${fieldPath} on response schema`,
        ).toBeTruthy();
      }
    }
  });

  it("does not reference undocumented SPA API routes", () => {
    const doc = loadOpenApiDoc();
    const references = collectSpaRouteReferences();

    for (const reference of references) {
      expect(
        doc.paths?.[reference.route],
        `${path.basename(reference.filePath)} references undocumented route ${reference.route}`,
      ).toBeTruthy();
    }
  });
});
