from typing import TypedDict, List, Optional


class AgentState(TypedDict):
    question:         str
    selected_table:   Optional[str]
    sql_query:        Optional[str]
    raw_data:         List[dict]
    columns:          List[str]
    data_error:       Optional[str]
    analytics:        Optional[dict]
    table_profile:    Optional[dict]
    data_context:     Optional[dict]
    llm_data_context: Optional[dict]
    intent:           Optional[dict]
    report_spec:      Optional[dict]
    linked_columns:   List[dict]
    explicit_aggregate_plan: Optional[dict]
    evidence_plan:    Optional[dict]
    evidence_results: List[dict]
    semantic_context: Optional[dict]
    semantic_proposal: Optional[dict]
    mschema:          Optional[dict]
    semantic_layer:   Optional[dict]
    semantic_gaps:    List[dict]
    answerability:    Optional[dict]
    output_type:      Optional[str]
    clarification_required: bool
    evidence_level:   Optional[str]
    warnings:         List[str]
    quality_issues:   List[dict]
    row_count_total:  Optional[int]
    report_path:      Optional[str]
    report_filename:  Optional[str]
    steps:            List[dict]
    final_answer:     Optional[str]
    critic_feedback:  Optional[str]
    critic_rounds:    int
