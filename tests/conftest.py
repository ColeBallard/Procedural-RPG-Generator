"""Shared fixtures for the test suite.

The suite is configured to issue real calls to the Grok LLM API. Tests that
make LLM calls depend on the ``grok_api_key`` fixture, which automatically
skips the test if no API key is available in the environment.

Required environment variables:
    GROK_API_KEY (or XAI_API_KEY)  -- API key for the xAI Grok endpoint.

Optional environment variables:
    GROK_TEST_MODEL  -- override the default model used by tests
                        (default: grok-4-1-fast-non-reasoning).
"""
import os
from datetime import datetime

import pytest
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.orm import Base, Seed
from app.services.gpt_service import GPTService

load_dotenv()

GROK_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = os.getenv("GROK_TEST_MODEL", "grok-4-1-fast-non-reasoning")


def _get_api_key():
    return os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")


@pytest.fixture(scope="session")
def grok_api_key():
    key = _get_api_key()
    if not key:
        pytest.skip(
            "GROK_API_KEY (or XAI_API_KEY) is not set; skipping live LLM test."
        )
    return key


@pytest.fixture(scope="session")
def grok_model():
    return DEFAULT_MODEL


@pytest.fixture(scope="session")
def openai_client(grok_api_key):
    return OpenAI(api_key=grok_api_key, base_url=GROK_BASE_URL)


@pytest.fixture
def gpt_service(openai_client, grok_model):
    return GPTService(openai_client, grok_model)


@pytest.fixture
def session_factory():
    """In-memory SQLite session factory with the full schema created.

    ``StaticPool`` + ``check_same_thread=False`` keep every session bound to
    the same underlying connection so the schema is visible from background
    threads (e.g. the worker thread spawned by the SSE world-building route).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def db_session(session_factory):
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seed_in_db(db_session):
    """Insert a Seed row so builders can reference it via ``seed_id``."""
    seed = Seed(
        id=1,
        current_turn=1,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(seed)
    db_session.commit()
    return seed
