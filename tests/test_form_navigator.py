from sortie.models import Job
from sortie.sites.forms import Field, step_prompt, validate_move

SNAPSHOT = {
    "headings": ["Voluntary self identification questions"],
    "buttons": [
        {"key": "b0", "text": "Save and close", "disabled": False},
        {"key": "b1", "text": "Proceed to review", "disabled": False},
        {"key": "b2", "text": "Submit your application", "disabled": False},
        {"key": "b3", "text": "Next", "disabled": True},
        {"key": "b4", "text": "Apply anyway", "disabled": False},
    ],
    "text": "Fields marked with * are required.",
}
FIELDS = [
    Field("f1", "consent", "You declare you have read the privacy notice *", ["Agree"], required=True),
    Field("f2", "consent", "Send me similar jobs", ["Yes"]),
    Field("f3", "radio", "Gender", ["Male", "Female", "Prefer not to say"]),
]


def test_allowed_moves():
    move = validate_move({"action": "click", "button": "b1", "why": "moves on"}, SNAPSHOT, FIELDS)
    assert (move.action, move.target, move.label) == ("click", "b1", "Proceed to review")
    assert validate_move({"action": "check", "field": "f1"}, SNAPSHOT, FIELDS).target == "f1"
    assert validate_move({"action": "skip_job", "why": "job closed"}, SNAPSHOT, FIELDS).why == "job closed"
    assert validate_move({"action": "wait"}, SNAPSHOT, FIELDS).action == "wait"


def test_guardrails():
    for button in ("b0", "b2", "b3", "b4", "b9"):  # save/close, submit, disabled, apply anyway, unknown key
        assert validate_move({"action": "click", "button": button}, SNAPSHOT, FIELDS) is None
    assert validate_move({"action": "check", "field": "f3"}, SNAPSHOT, FIELDS) is None  # not a consent box
    assert validate_move({"action": "fill", "field": "f3", "value": "Male"}, SNAPSHOT, FIELDS) is None
    assert validate_move({}, SNAPSHOT, FIELDS) is None


def test_step_prompt_lists_fields_and_buttons():
    text = step_prompt({**SNAPSHOT, "errors": ["Choose an option to continue."]}, FIELDS,
                       Job("indeed", "1", "u", "Backend Engineer", "Acme"))
    assert '[f1] consent "You declare you have read the privacy notice *" (empty, required) options: Agree' in text
    assert '[b3] "Next" (disabled)' in text and "Errors shown: Choose an option to continue." in text
