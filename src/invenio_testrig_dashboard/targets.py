from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Target as TargetRow
from .settings import Target as TargetConfig


@dataclass
class TargetSyncChanges:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


def sync_targets(session: Session, configured_targets: Iterable[TargetConfig]) -> TargetSyncChanges:
    """Upsert configured targets; disable (never delete) the ones no longer configured."""
    configured = {target.branch: target for target in configured_targets}
    existing = {
        row.branch: row
        for row in session.scalars(select(TargetRow).execution_options(populate_existing=True))
    }

    changes = TargetSyncChanges()
    for branch, target_config in configured.items():
        row = existing.get(branch)
        if row is None:
            changes.added.append(branch)
        elif row.schedule != target_config.schedule or row.enabled != target_config.enabled:
            changes.updated.append(branch)

    changes.disabled = sorted(
        branch for branch, row in existing.items() if row.enabled and branch not in configured
    )

    if configured:
        values = [
            {"branch": target.branch, "schedule": target.schedule, "enabled": target.enabled}
            for target in configured.values()
        ]
        statement = insert(TargetRow).values(values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[TargetRow.branch],
                set_={
                    "schedule": statement.excluded.schedule,
                    "enabled": statement.excluded.enabled,
                    "updated_at": func.now(),
                },
                # Only touch rows that really changed, so updated_at means "last change".
                where=or_(
                    TargetRow.schedule != statement.excluded.schedule,
                    TargetRow.enabled != statement.excluded.enabled,
                ),
            )
        )

    if changes.disabled:
        session.execute(
            update(TargetRow)
            .where(TargetRow.branch.in_(changes.disabled))
            .values(enabled=False, updated_at=func.now())
        )

    return changes
