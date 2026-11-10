from datetime import date

from sortie.llm.roles import clean_roles, suggest_roles


class FakeLLM:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls, self.model = reply, fail, 0, "fake"

    @property
    def enabled(self):
        return True

    async def complete_json(self, system, user):
        self.calls += 1
        if self.fail:
            raise TimeoutError()
        return self.reply


def _search(search, **kw):
    return search.model_copy(update={"ai_roles": 3, **kw})


def test_clean_roles(search):
    s = _search(search, roles=["Python Backend Developer"], exclude_keywords=["lead", "manager"])
    got = clean_roles(["python backend developer", "Spring Boot Developer", "Tech Lead Java", "  Kafka   Engineer ",
                       "Spring Boot Developer", "A very long title that goes on and on and on and on forever",
                       "Microservices Engineer", "Backend Engineer"], s)
    assert got == ["Spring Boot Developer", "Microservices Engineer", "Backend Engineer"]  # tool title dropped, capped at 3


async def test_suggestions_cached_for_the_day(profile, search, tmp_path):
    s, cache = _search(search), tmp_path / "ai_roles.json"
    llm = FakeLLM({"roles": ["Spring Boot Developer", "Java Microservices Engineer"]})
    first = await suggest_roles(llm, profile, s, cache=cache, today=date(2026, 11, 10))
    again = await suggest_roles(llm, profile, s, cache=cache, today=date(2026, 11, 10))
    assert first == again == ["Spring Boot Developer", "Java Microservices Engineer"] and llm.calls == 1
    await suggest_roles(llm, profile, s, cache=cache, today=date(2026, 11, 11))  # new day
    await suggest_roles(llm, profile, s.model_copy(update={"roles": s.roles + ["Go Developer"]}), cache=cache,
                        today=date(2026, 11, 11))  # settings changed
    assert llm.calls == 3


async def test_off_or_failing_llm_means_your_roles_only(profile, search, tmp_path):
    cache = tmp_path / "ai_roles.json"
    assert await suggest_roles(FakeLLM(fail=True), profile, _search(search), cache=cache) == []
    off = FakeLLM({"roles": ["X Developer"]})
    assert await suggest_roles(off, profile, _search(search, ai_roles=0), cache=cache) == [] and off.calls == 0


def test_rewordings_and_tool_titles_dropped(search):
    s = _search(search, roles=["Python Backend Developer"], exclude_keywords=[])
    got = clean_roles(["Backend Engineer Python", "Kafka Engineer", "Redis Developer", "Backend Engineer",
                       "Java Kafka Developer", "AI Engineer"], s)
    assert got == ["Backend Engineer", "Java Kafka Developer", "AI Engineer"]
