"""Best-effort URL resolution shared by dry-run previews and audit logging.

Both the dry-run renderer and the audit-log wrapper need to record the
fully-qualified URL that ``bcli`` is about to hit (or did hit). Two
constraints make a separate helper worthwhile:

* The resolver must NEVER raise. A broken URL build path shouldn't crash
  the user's actual command — the preview / audit just records ``None``
  and gets out of the way.
* The ``--standard`` escape hatch on ``attach upload`` bypasses the
  registry. Code that resolves the URL for previewing or auditing has
  to mirror that bypass; otherwise the preview shows the registry's
  custom route while the actual upload silently uses ``/api/v2.0/``.
"""

from __future__ import annotations

from bcli_cli._state import state


def try_resolve_url(
    endpoint: str,
    *,
    record_id: str | None = None,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
    force_standard: bool = False,
) -> str | None:
    """Resolve ``endpoint`` to a full URL using the active profile.

    Returns ``None`` on any failure (registry miss with
    ``disable_standard_api``, missing company id, malformed profile,
    etc.). Callers should treat ``None`` as "preview the rest, the user
    will see the gap and correct".
    """
    try:
        if force_standard:
            from bcli._url import build_url

            profile = state.profile
            return build_url(
                environment=profile.environment,
                company_id=profile.company_id or "",
                entity_set_name=endpoint,
                record_id=record_id,
            )
        client = state.make_async_client()
        return client._resolve_url(
            endpoint,
            record_id=record_id,
            publisher=publisher,
            group=group,
            version=version,
        )
    except Exception:
        return None
