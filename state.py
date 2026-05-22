from typing import TypedDict, List, Optional


class AgentState(TypedDict):
    question:         str
    selected_table:   Optional[str]
    sql_query:        Optional[str]
    raw_data:         List[dict]
    columns:          List[str]
    data_error:       Optional[str]
    analytics:        Optional[dict]
    report_path:      Optional[str]
    report_filename:  Optional[str]
    steps:            List[dict]
    final_answer:     Optional[str]
    critic_feedback:  Optional[str]
    critic_rounds:    int
