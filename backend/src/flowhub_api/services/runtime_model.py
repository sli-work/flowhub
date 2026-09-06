"""Budget enforcement for every model and tool-bound request."""
from langchain_core.utils.function_calling import convert_to_openai_tool
from flowhub_api.services.context_budget import budget_for_model, enforce_message_budget


class BudgetedModel:
    def __init__(self, inner, budget, tools=None):
        self.inner, self.budget, self.tools = inner, budget, tools

    def bind_tools(self, tools):
        return BudgetedModel(self.inner.bind_tools(tools), self.budget,
                             [convert_to_openai_tool(tool) for tool in tools])

    async def ainvoke(self, messages):
        enforce_message_budget(messages, self.budget, self.tools)
        return await self.inner.ainvoke(messages)

    async def astream(self, messages):
        enforce_message_budget(messages, self.budget, self.tools)
        stream = self.inner.astream(messages)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            close = getattr(stream, 'aclose', None)
            if close:
                await close()


def make_model(factory, model, provider, key, retries=2):
    budget = budget_for_model(model, provider)
    return BudgetedModel(factory(model=model.model, base_url=provider.base_url, api_key=key,
        temperature=0, timeout=120, max_retries=retries, max_tokens=budget.output_tokens), budget)
