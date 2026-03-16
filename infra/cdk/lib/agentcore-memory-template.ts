export const TENANT_MEMORY_TEMPLATE_PARAMETER_NAME =
  '/platform/agentcore/memory/template/default';

export interface SemanticMemoryTemplate {
  readonly strategy: 'SEMANTIC';
  readonly strategyNameTemplate: string;
  readonly namespaceTemplate: string;
  readonly descriptionTemplate: string;
}

export interface AgentCoreTenantMemoryTemplate {
  readonly provisionedBy: 'TenantStack';
  readonly eventExpiryDurationDays: number;
  readonly semanticMemory: SemanticMemoryTemplate;
}

export const DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE: AgentCoreTenantMemoryTemplate = {
  provisionedBy: 'TenantStack',
  eventExpiryDurationDays: 90,
  semanticMemory: {
    strategy: 'SEMANTIC',
    strategyNameTemplate: 'TenantSemanticMemory',
    namespaceTemplate: 'tenant/{tenantId}',
    descriptionTemplate: 'Per-tenant AgentCore memory for {tenantId}',
  },
};

const renderTemplate = (template: string, tenantId: string): string =>
  template.split('{tenantId}').join(tenantId);

export const serializeAgentCoreTenantMemoryTemplate = (
  template: AgentCoreTenantMemoryTemplate = DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE,
): string => JSON.stringify(template);

export const resolveTenantMemoryProperties = (
  tenantId: string,
  template: AgentCoreTenantMemoryTemplate = DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE,
) => ({
  description: renderTemplate(template.semanticMemory.descriptionTemplate, tenantId),
  eventExpiryDuration: template.eventExpiryDurationDays,
  memoryStrategies: [
    {
      SemanticMemoryStrategy: {
        Name: template.semanticMemory.strategyNameTemplate,
        Description: renderTemplate(template.semanticMemory.descriptionTemplate, tenantId),
        Namespaces: [renderTemplate(template.semanticMemory.namespaceTemplate, tenantId)],
        Type: template.semanticMemory.strategy,
      },
    },
  ],
});
