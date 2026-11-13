import hashlib
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .status import PackageStatus, RunStatus, RunTrigger

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enum(enum_cls: type[StrEnum], name: str = "status") -> SAEnum:
    # varchar + CHECK instead of a Postgres native enum: readable and easy to extend.
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Target(Base):
    __tablename__ = "target"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    branch: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    schedule: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Run(Base):
    __tablename__ = "run"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False, default=uuid.uuid4)
    target_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("target.id"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus), nullable=False)
    trigger: Mapped[RunTrigger] = mapped_column(_enum(RunTrigger, "trigger"), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(Text)
    testrig_ref: Mapped[str] = mapped_column(Text, nullable=False)
    testrig_config: Mapped[str | None] = mapped_column(Text)
    gh_run_id: Mapped[int | None] = mapped_column(BigInteger)
    gh_run_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The run notification is posted once, after the commit. NULL means "not posted yet",
    # so the next ingest tick retries it instead of losing a regression.
    alerts_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    packages_total: Mapped[int | None] = mapped_column(Integer)
    packages_failed: Mapped[int | None] = mapped_column(Integer)
    tests_total: Mapped[int | None] = mapped_column(Integer)
    tests_failed: Mapped[int | None] = mapped_column(Integer)
    tests_skipped: Mapped[int | None] = mapped_column(Integer)
    # Live progress while the run is running, read from GitHub's job list. Kept after
    # the run ends; the UI shows it only for a running run. progress_at NULL = never read.
    progress_total: Mapped[int | None] = mapped_column(Integer)
    progress_done: Mapped[int | None] = mapped_column(Integer)
    progress_failed: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_run_target_id_created_at", "target_id", "created_at"),
        # A running run is always following a GitHub run (ingest relies on it).
        CheckConstraint(
            "status <> 'running' OR gh_run_id IS NOT NULL", name="running_has_gh_run_id"
        ),
    )


Index(
    "ix_run_active_status",
    Run.__table__.c.status,
    postgresql_where=Run.__table__.c.status.in_(
        [RunStatus.REQUESTED, RunStatus.QUEUED, RunStatus.RUNNING]
    ),
)


class AlertSent(Base):
    """Deduplication for ops alerts: one row per key, with the last time it was posted."""

    __tablename__ = "alert_sent"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PackageResult(Base):
    __tablename__ = "package_result"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    package: Mapped[str] = mapped_column(Text, nullable=False)
    # The report directory and the side (original/patched) that was parsed; nullable for rows
    # ingested before artifact paths were recorded, where the API falls back to testrig's path convention.
    artifact_dir: Mapped[str | None] = mapped_column(Text)
    side: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(Text)
    git_ref: Mapped[str | None] = mapped_column(Text)
    git_commit: Mapped[str | None] = mapped_column(Text)
    commits_ahead: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[PackageStatus] = mapped_column(_enum(PackageStatus), nullable=False)
    has_junit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float)
    tests_total: Mapped[int | None] = mapped_column(Integer)
    tests_failed: Mapped[int | None] = mapped_column(Integer)
    tests_skipped: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("run_id", "package", name="uq_package_result_run_id_package"),
    )


def case_key(package: str, classname: str, name: str) -> bytes:
    """The identity of a test case: SHA-256 of ``package NUL classname NUL name`` (UTF-8).

    Names can be ~10 KB long, far more than a btree index entry allows, so only this
    32-byte hash is indexed; the full names live in unindexed text columns. This is the
    only place the key is built. (Postgres text cannot contain NUL, so the separator is
    unambiguous.) Migration 0002 computes the same value in SQL; a test keeps them equal.
    """
    raw = b"\x00".join(part.encode("utf-8") for part in (package, classname, name))
    return hashlib.sha256(raw).digest()


class TestCase(Base):
    __test__ = False  # keep pytest from collecting the model as a test class

    __tablename__ = "test_case"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    package: Mapped[str] = mapped_column(Text, nullable=False)
    classname: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # case_key(package, classname, name); the only indexed identity of a test case.
    key_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    first_seen_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("run.id"), nullable=False)

    __table_args__ = (CheckConstraint("octet_length(key_hash) = 32", name="key_hash_length"),)


class TestResult(Base):
    __test__ = False  # keep pytest from collecting the model as a test class

    __tablename__ = "test_result"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("run.id", ondelete="CASCADE"), primary_key=True
    )
    test_case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("test_case.id"), primary_key=True
    )
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    duration_s: Mapped[float | None] = mapped_column(REAL)
    message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status BETWEEN 0 AND 3", name="status_range"),
        Index("ix_test_result_test_case_id_run_id", "test_case_id", "run_id"),
    )
