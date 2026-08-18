from .calls import analyze_call, is_call_scenario
from .transcription import analyze_transcription


def analyze_case(records, activity_id):
    if is_call_scenario(records, activity_id):
        return analyze_call(records, activity_id)

    analysis = analyze_transcription(records, activity_id)
    # Динамические метаданные нужны общему шаблону, не меняя проверенную
    # модель результата сценария встречи.
    analysis.scenario = "meeting"
    analysis.scenario_label = "Встреча"
    analysis.task_type_code = None
    return analysis


__all__ = [
    "analyze_call",
    "analyze_case",
    "analyze_transcription",
    "is_call_scenario",
]
