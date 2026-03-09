import fs from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import YAML from "yaml";

import { SPA_OPENAPI_CONTRACTS } from "./contracts";

type OpenApiDoc = {
  paths: Record<string, Record<string, any>>;
  components?: {
    schemas?: Record<string, any>;
  };
};

function loadOpenApiDoc(): OpenApiDoc {
  const specPath = fileURLToPath(new URL("../../../docs/openapi.yaml", import.meta.url));
  const yaml = fs.readFileSync(specPath, "utf8");
  return YAML.parse(yaml) as OpenApiDoc;
}

function resolveSchema(schema: any, doc: OpenApiDoc): any {
  if (!schema || typeof schema !== "object") {
    return schema;
  }
  if (!schema.$ref) {
    return schema;
  }

  const pointer = String(schema.$ref).replace(/^#\//, "").split("/");
  let resolved: any = doc;
  for (const segment of pointer) {
    resolved = resolved?.[segment];
  }
  if (!resolved) {
    throw new Error(`Unresolvable OpenAPI reference: ${schema.$ref}`);
  }
  return resolveSchema(resolved, doc);
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
        for (const field of contract.requiredFields) {
          expect(
            itemSchema?.properties?.[field],
            `${contract.name}: missing field ${field} on collection item schema`,
          ).toBeTruthy();
        }
        continue;
      }

      const resolvedRoot = resolveSchema(responseSchema, doc);
      for (const field of contract.requiredFields) {
        expect(
          resolvedRoot?.properties?.[field],
          `${contract.name}: missing field ${field} on response schema`,
        ).toBeTruthy();
      }
    }
  });
});
