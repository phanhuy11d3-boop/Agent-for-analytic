from langgraph.graph import StateGraph, END
from state import AgentState
from agents.semantic_agent  import semantic_agent_node
from agents.data_agent      import data_agent_node
from agents.analytics_agent import analytics_agent_node
from agents.critic_agent    import critic_agent_node, MAX_CRITIC_ROUNDS
from agents.writer_agent    import writer_agent_node


def _route_after_data(state: AgentState) -> str:
    if state.get("data_error") or not state.get("raw_data"):
        return "end"
    return "analytics_agent"


def _route_after_semantic(state: AgentState) -> str:
    if state.get("clarification_required"):
        return "writer_agent"
    return "data_agent"


def _route_after_critic(state: AgentState) -> str:
    if state.get("critic_feedback") and state.get("critic_rounds", 0) <= MAX_CRITIC_ROUNDS:
        return "data_agent"
    return "writer_agent"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("semantic_agent",  semantic_agent_node)
    graph.add_node("data_agent",      data_agent_node)
    graph.add_node("analytics_agent", analytics_agent_node)
    graph.add_node("critic_agent",    critic_agent_node)
    graph.add_node("writer_agent",    writer_agent_node)

    graph.set_entry_point("semantic_agent")

    graph.add_conditional_edges(
        "semantic_agent",
        _route_after_semantic,
        {"writer_agent": "writer_agent", "data_agent": "data_agent"},
    )

    graph.add_conditional_edges(
        "data_agent",
        _route_after_data,
        {"analytics_agent": "analytics_agent", "end": END},
    )
    graph.add_edge("analytics_agent", "critic_agent")
    graph.add_conditional_edges(
        "critic_agent",
        _route_after_critic,
        {"data_agent": "data_agent", "writer_agent": "writer_agent"},
    )
    graph.add_edge("writer_agent", END)

    return graph.compile()


APP_GRAPH = build_graph()

_INITIAL_STATE = {
    "selected_table":  None,
    "sql_query":       None,
    "raw_data":        [],
    "columns":         [],
    "data_error":      None,
    "analytics":       None,
    "table_profile":   None,
    "data_context":    None,
    "llm_data_context": None,
    "intent":          None,
    "report_spec":     None,
    "linked_columns":  [],
    "evidence_plan":   None,
    "evidence_results": [],
    "semantic_context": None,
    "mschema":         None,
    "semantic_layer":  None,
    "semantic_gaps":   [],
    "clarification_required": False,
    "evidence_level":  None,
    "warnings":        [],
    "quality_issues":  [],
    "row_count_total": None,
    "report_path":     None,
    "report_filename": None,
    "steps":           [],
    "final_answer":    None,
    "critic_feedback": None,
    "critic_rounds":   0,
}


def run(question: str, selected_table: str | None = None) -> AgentState:
    return APP_GRAPH.invoke({**_INITIAL_STATE, "question": question, "selected_table": selected_table})


def stream(question: str, selected_table: str | None = None):
    """Yield (node_name, partial_state) for each agent step."""
    for chunk in APP_GRAPH.stream({**_INITIAL_STATE, "question": question, "selected_table": selected_table}):
        node_name = list(chunk.keys())[0]
        yield node_name, chunk[node_name]
