"""Closed owner controls; runtime pins and schedule positions are host-owned."""
import improvement_cases as IC
from improvement_experiments import _generation, _identifier
from improvement_trial_requests import TrialRequestError, _json_tree

_FIELDS = {
    "create": {"experiment_id", "contract_id", "case_set_id", "base_bundle_id", "expected_context_generation", "repeat_policy", "timeout_seconds"},
    "bind": {"candidate_id", "authority_id", "expected_generation"},
    "freeze": {"ordinal", "kind", "expected_generation"},
    "next": {"comparison_id", "expected_generation"},
    "report": {"comparison_id", "expected_generation"},
    "select": {"choice", "ordinal", "expected_generation"},
    "cancel": {"expected_generation"}, "resume": {"expected_generation"}, "recover": set(),
}


def validate(value, operation):
    try:
        _json_tree(value)
        IC._fields(value, _FIELDS[operation] | {"event_key"})
        if type(value["event_key"]) is not str or not 8 <= len(value["event_key"]) <= 160:
            raise ValueError("invalid_event_key")
        for key in ("experiment_id", "contract_id", "case_set_id", "base_bundle_id", "candidate_id", "authority_id", "comparison_id"):
            if key in value:
                _identifier(value[key])
        for key in ("expected_generation", "expected_context_generation"):
            if key in value:
                _generation(value[key])
        if operation == "create":
            if (type(value["repeat_policy"]) is not str or value["repeat_policy"] not in ("feasibility", "pilot")
                    or type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 300):
                raise ValueError("invalid_experiment_request")
        if operation == "freeze":
            if type(value["ordinal"]) is not int or value["ordinal"] not in (1, 2) or value["kind"] not in ("tuning", "final"):
                raise ValueError("invalid_experiment_comparison")
        if operation == "select":
            if not ((value["choice"] == "keep_base" and value["ordinal"] is None)
                    or (value["choice"] == "candidate" and type(value["ordinal"]) is int and value["ordinal"] in (1, 2))):
                raise ValueError("invalid_experiment_selection")
        return IC._frozen(value)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise TrialRequestError() from None
