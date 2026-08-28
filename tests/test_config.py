from conference_scout_mcp.config import Settings


def test_postgres_urls_use_psycopg3_driver() -> None:
    assert (
        Settings(database_url="postgresql://user:pass@host/db").storage_url
        == "postgresql+psycopg://user:pass@host/db"
    )
    assert (
        Settings(database_url="postgres://user:pass@host/db").storage_url
        == "postgresql+psycopg://user:pass@host/db"
    )


def test_explicit_driver_and_encoded_password_are_preserved() -> None:
    url = "postgresql+psycopg://user:p%40ss@host/db"
    assert Settings(database_url=url).storage_url == url
