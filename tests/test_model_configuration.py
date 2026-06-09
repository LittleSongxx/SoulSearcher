from agent.runtime.request_builder import ResearchRuntimeRequest, build_research_runtime
from common.config import Settings, apply_app_config_overrides


def test_settings_keep_env_models_and_inherit_tiers(tmp_path, monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_BASE_URL=https://api.deepseek.com",
                "PRIMARY_MODEL=deepseek-v4-flash",
                "REASONING_MODEL=deepseek-v4-pro",
            ]
        ),
        encoding="utf-8",
    )
    yaml_path = tmp_path / "config.yaml"
    yaml_path.with_suffix(".yaml.example").write_text(
        "llm:\n  primary_model: \"\"\n  openai_base_url: \"\"\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    settings.yaml_config_path = str(yaml_path)

    apply_app_config_overrides(settings)

    assert settings.openai_base_url == "https://api.deepseek.com"
    assert settings.primary_model == "deepseek-v4-flash"
    assert settings.reasoning_model == "deepseek-v4-pro"
    assert settings.fast_llm_model == "deepseek-v4-flash"
    assert settings.smart_llm_model == "deepseek-v4-flash"
    assert settings.strategic_llm_model == "deepseek-v4-pro"


def test_request_model_is_used_as_workflow_tier_default(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_RESEARCH_WORKSPACE_PATH", str(tmp_path / "workspaces"))

    bundle = build_research_runtime(
        ResearchRuntimeRequest(
            input_text="test query",
            thread_id="thread_test",
            model="deepseek-v4-pro",
            mode_info={"use_deep": True},
            user_id="user_test",
            images=[],
            research_brief=None,
            context_messages=[],
            deepsearch_config={},
            base_configurable={"thread_id": "thread_test", "model": "deepseek-v4-pro"},
        )
    )

    configurable = bundle.config["configurable"]
    assert configurable["fast_llm"] == "deepseek-v4-pro"
    assert configurable["smart_llm"] == "deepseek-v4-pro"
    assert configurable["strategic_llm"] == "deepseek-v4-pro"


def test_explicit_tier_overrides_take_precedence_over_request_model(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_RESEARCH_WORKSPACE_PATH", str(tmp_path / "workspaces"))

    bundle = build_research_runtime(
        ResearchRuntimeRequest(
            input_text="test query",
            thread_id="thread_test_override",
            model="deepseek-v4-pro",
            mode_info={"use_deep": True},
            user_id="user_test",
            images=[],
            research_brief=None,
            context_messages=[],
            deepsearch_config={"smart_llm": "deepseek-v4-flash"},
            base_configurable={"thread_id": "thread_test_override"},
        )
    )

    configurable = bundle.config["configurable"]
    assert configurable["fast_llm"] == "deepseek-v4-pro"
    assert configurable["smart_llm"] == "deepseek-v4-flash"
    assert configurable["strategic_llm"] == "deepseek-v4-pro"
