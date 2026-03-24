from miso.characters import (
    CharacterAgent,
    CharacterSpec,
    decide_character_response,
    evaluate_character,
    make_character_direct_session_id,
    make_character_relationship_namespace,
    make_character_self_namespace,
)


def _character(**overrides):
    base = {
        "id": "office_friend",
        "name": "Mina",
        "gender": "female",
        "role": "product manager",
        "persona": "observant and busy",
        "speaking_style": ["direct", "polite"],
        "talkativeness": 0.55,
        "politeness": 0.7,
        "autonomy": 0.6,
        "timezone": "America/Vancouver",
        "schedule": {
            "timezone": "America/Vancouver",
            "default_status": "free",
            "blocks": [
                {
                    "days": ["weekday"],
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "status": "working",
                    "availability": "limited",
                    "interruption_tolerance": 0.45,
                },
                {
                    "days": ["weekday"],
                    "start_time": "13:00",
                    "end_time": "15:00",
                    "status": "meeting",
                    "availability": "busy",
                    "interruption_tolerance": 0.05,
                },
                {
                    "days": ["weekday"],
                    "start_time": "23:00",
                    "end_time": "07:00",
                    "status": "sleeping",
                    "availability": "offline",
                    "interruption_tolerance": 0.0,
                },
            ],
        },
    }
    base.update(overrides)
    return base


def test_schedule_evaluation_respects_timezone_and_busy_blocks():
    evaluation = evaluate_character(
        _character(),
        now="2026-03-23T18:30:00+00:00",
    )

    assert evaluation.timezone == "America/Vancouver"
    assert evaluation.status == "working"
    assert evaluation.availability == "limited"
    assert evaluation.interruption_tolerance == 0.45
    assert evaluation.active_schedule_block["status"] == "working"

    meeting_evaluation = evaluate_character(
        _character(),
        now="2026-03-23T13:30:00-07:00",
    )
    assert meeting_evaluation.status == "meeting"
    assert meeting_evaluation.availability == "busy"
    assert meeting_evaluation.available_at is not None


def test_schedule_evaluation_handles_offline_overnight_blocks():
    evaluation = evaluate_character(
        _character(),
        now="2026-03-24T06:15:00-07:00",
    )

    assert evaluation.status == "sleeping"
    assert evaluation.availability == "offline"
    assert evaluation.interruption_tolerance == 0.0


def test_character_decision_can_reply_when_available():
    decision = decide_character_response(
        _character(
            talkativeness=0.8,
            politeness=0.7,
            autonomy=0.25,
        ),
        now="2026-03-23T17:30:00-07:00",
    )

    assert decision.action == "reply"
    assert decision.should_reply is True


def test_character_decision_can_defer_when_busy_but_polite():
    decision = decide_character_response(
        _character(
            politeness=0.8,
            autonomy=0.7,
            talkativeness=0.35,
        ),
        now="2026-03-23T13:30:00-07:00",
    )

    assert decision.action == "defer"
    assert decision.should_reply is False
    assert "busy" in (decision.courtesy_message or "").lower()


def test_character_decision_can_ignore_when_reserved_and_busy():
    decision = decide_character_response(
        _character(
            politeness=0.1,
            autonomy=0.95,
            talkativeness=0.05,
        ),
        now="2026-03-23T13:30:00-07:00",
    )

    assert decision.action == "ignore"
    assert decision.should_reply is False
    assert decision.courtesy_message is None


def test_character_memory_keys_are_filesystem_safe_and_distinct():
    character_id = "Mina:Office/PM"
    thread_id = "General Chat #42"
    human_id = "Local User"

    self_namespace = make_character_self_namespace(character_id)
    relationship_namespace = make_character_relationship_namespace(
        character_id,
        human_id,
    )
    session_id = make_character_direct_session_id(character_id, thread_id)

    assert self_namespace == "character_mina_office_pm__self"
    assert relationship_namespace == "character_mina_office_pm__rel__local_user"
    assert session_id == "character_mina_office_pm__dm__general_chat_42"
    assert self_namespace != relationship_namespace
    assert relationship_namespace not in session_id


def test_character_agent_build_config_keeps_self_and_relationship_profiles_separate():
    profiles = {
        "character_office_friend__self": {"history": "likes product strategy"},
        "character_office_friend__rel__local_user": {"familiarity": "new stranger"},
    }

    config = CharacterAgent.build_config(
        character=CharacterSpec.coerce(_character()),
        thread_id="main thread",
        human_id="local_user",
        profile_loader=lambda namespace: profiles.get(namespace, {}),
        now="2026-03-23T11:00:00-07:00",
    )

    assert config["session_id"] == "character_office_friend__dm__main_thread"
    assert config["run_memory_namespace"] == "character_office_friend__rel__local_user"
    assert config["self_profile"] == {"history": "likes product strategy"}
    assert config["relationship_profile"] == {"familiarity": "new stranger"}
    assert "Self profile" in config["instructions"]
    assert "Relationship profile" in config["instructions"]
    assert "likes product strategy" in config["instructions"]
    assert "new stranger" in config["instructions"]
