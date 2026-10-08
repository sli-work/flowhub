"""Budget enforcement for every model and tool-bound request."""
from langchain_core.utils.function_calling import convert_to_openai_tool
from flowhub_api.services.context_budget import ContextBudget, budget_for_model, enforce_message_budget


class BudgetedModel:
    def __init__(self, inner, budget, tools=None):
        self.inner, self.budget, self.tools = inner, budget, tools

    def bind_tools(self, tools):
        return BudgetedModel(self.inner.bind_tools(tools), self.budget,
                             [convert_to_openai_tool(tool) for tool in tools])

    def with_max_output_tokens(self, max_output_tokens: int):
        """Return a request-scoped wrapper for terse tool-selection responses."""
        limit = max(1, min(int(max_output_tokens), self.budget.output_tokens))
        budget = ContextBudget(self.budget.context_tokens, limit, self.budget.model_name)
        return BudgetedModel(self.inner.bind(max_tokens=limit), budget, self.tools)

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


def make_model(factory, model, provider, key, retries=2, response_format=None, *, send_max_tokens=True):
    budget = budget_for_model(model, provider)
    kwargs = {
        "model": model.model,
        "base_url": provider.base_url,
        "api_key": key,
        "temperature": 0,
        "timeout": 120,
        "max_retries": retries,
    }
    if send_max_tokens:
        kwargs["max_tokens"] = budget.output_tokens
    else:
        # The provider owns completion length for unbounded task deliveries.
        # Keep only its configured context-window guard locally.
        budget = ContextBudget(budget.context_tokens, 0, budget.model_name)
    if response_format is not None:
        kwargs["model_kwargs"] = {"response_format": response_format}
    return BudgetedModel(factory(**kwargs), budget)
