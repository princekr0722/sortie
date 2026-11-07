from sortie.terminal import clean_reply, match_options


def test_clean_reply_strips_arrow_keys():
    assert clean_reply("\x1b[D\x1b[D") == ""
    assert clean_reply("  Ye\x1b[Ds \n") == "Yes"


def test_match_options():
    assert match_options("1", ["Yes", "No"]) == "Yes"
    assert match_options("no", ["Yes", "No"]) == "No"
    assert match_options("1, 3", ["Kafka", "Redis", "Docker"]) == "Kafka, Docker"
    assert match_options("maybe", ["Yes", "No"]) is None
    assert match_options("5", ["Yes", "No"]) is None
