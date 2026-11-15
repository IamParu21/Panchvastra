"""URL slugs for products, categories and sub-categories.

Rules (match db_changes/2026_11_16_slugs.sql):
* slug = lowercase ASCII letters/digits separated by single "-", max 150.
* Unique among live rows: products and categories store-wide, sub-categories
  within their category (scope_id = category_id).
* A slug is set once from the name and does not follow renames. When an admin
  changes it, the old one goes to slug_redirects so old links still resolve.
* A slug that is another item's old slug counts as taken, so a redirect can
  never start pointing at a different item.
"""
import re
import unicodedata

from rest_framework import status
from rest_framework.exceptions import ValidationError


SLUG_MAX_LENGTH = 150

PRODUCT = "PRODUCT"
CATEGORY = "CATEGORY"
SUB_CATEGORY = "SUB_CATEGORY"

_ENTITIES = {
    PRODUCT: {"table": "public.products", "scope_column": None, "fallback": "product", "label": "product"},
    CATEGORY: {"table": "public.categories", "scope_column": None, "fallback": "category", "label": "category"},
    SUB_CATEGORY: {"table": "public.sub_categories", "scope_column": "category_id", "fallback": "collection", "label": "sub category in this category"},
}


class SlugConflict(Exception):
    """A slug the admin typed is already used. Carries the API response."""

    def __init__(self, entity_type, slug):
        self.payload = {
            "message": f"The URL slug '{slug}' is already used by another {_ENTITIES[entity_type]['label']}.",
            "data": {"slug": slug}
        }
        self.status_code = status.HTTP_409_CONFLICT


def slugify_text(text):
    """'Bamboo Piece (Off-White)' -> 'bamboo-piece-off-white'. Accents are
    folded to ASCII; anything else non-alphanumeric becomes a separator."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-")


def normalize_requested_slug(value):
    """An admin-typed slug, normalized ('Bamboo Piece' -> 'bamboo-piece').
    400 when nothing usable is left."""
    slug = slugify_text(value)

    if not slug:
        raise ValidationError({"slug": "Use at least one letter or number."})

    return slug


def _scope_sql(entity_type):
    column = _ENTITIES[entity_type]["scope_column"]
    return f" AND {column} = %s" if column else ""


def _scope_params(entity_type, scope_id):
    return [scope_id] if _ENTITIES[entity_type]["scope_column"] else []


def _used_slugs(cursor, entity_type, base, scope_id, exclude_id):
    """Every live slug and every other item's old slug that equals base or
    looks like base-N, in two indexed queries. base only ever contains
    [a-z0-9-], so it is safe inside LIKE."""
    table = _ENTITIES[entity_type]["table"]
    like = base + "-%"

    cursor.execute(
        f"""
        SELECT slug FROM {table}
        WHERE is_deleted = FALSE AND (slug = %s OR slug LIKE %s){_scope_sql(entity_type)}
        AND id IS DISTINCT FROM %s
        """,
        [base, like] + _scope_params(entity_type, scope_id) + [exclude_id]
    )
    used = {row[0] for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT old_slug FROM public.slug_redirects
        WHERE entity_type = %s AND scope_id = %s AND (old_slug = %s OR old_slug LIKE %s)
        AND entity_id IS DISTINCT FROM %s
        """,
        [entity_type, scope_id or 0, base, like, exclude_id]
    )
    used.update(row[0] for row in cursor.fetchall())

    return used


def is_slug_taken(cursor, entity_type, slug, scope_id=0, exclude_id=None):
    return slug in _used_slugs(cursor, entity_type, slug, scope_id, exclude_id)


def generate_unique_slug(cursor, entity_type, source_text, scope_id=0, exclude_id=None):
    """Free slug from a name: 'bamboo-piece', else 'bamboo-piece-2', '-3', ..."""
    base = slugify_text(source_text) or _ENTITIES[entity_type]["fallback"]
    used = _used_slugs(cursor, entity_type, base, scope_id, exclude_id)

    if base not in used:
        return base

    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[:SLUG_MAX_LENGTH - len(suffix)].rstrip("-") + suffix
        if candidate not in used:
            return candidate
        n += 1


def resolve_requested_slug(cursor, entity_type, requested, source_text, scope_id=0, exclude_id=None):
    """Slug for a create: the admin's own (normalized; 409 if taken) or one
    generated from the name."""
    if requested:
        slug = normalize_requested_slug(requested)

        if is_slug_taken(cursor, entity_type, slug, scope_id, exclude_id):
            raise SlugConflict(entity_type, slug)

        return slug

    return generate_unique_slug(cursor, entity_type, source_text, scope_id, exclude_id)


def record_slug_change(cursor, entity_type, entity_id, old_slug, old_scope_id, new_slug, new_scope_id):
    """Call after the row's slug (or its scope) changed: remember the old slug
    for redirects, and drop this item's own redirect for the new slug if it is
    reclaiming one of its earlier slugs."""
    if old_slug == new_slug and (old_scope_id or 0) == (new_scope_id or 0):
        return

    cursor.execute(
        """
        INSERT INTO public.slug_redirects (entity_type, scope_id, old_slug, entity_id, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (entity_type, scope_id, old_slug)
        DO UPDATE SET entity_id = EXCLUDED.entity_id, created_at = NOW()
        """,
        [entity_type, old_scope_id or 0, old_slug, entity_id]
    )

    cursor.execute(
        """
        DELETE FROM public.slug_redirects
        WHERE entity_type = %s AND scope_id = %s AND old_slug = %s AND entity_id = %s
        """,
        [entity_type, new_scope_id or 0, new_slug, entity_id]
    )


def find_id_by_slug(cursor, entity_type, slug, scope_id=0):
    """Id of the live item with this slug, or with this as an old slug.
    None when neither exists (or the item was deleted)."""
    slug = (slug or "").strip().lower()

    if not slug:
        return None

    table = _ENTITIES[entity_type]["table"]

    cursor.execute(
        f"SELECT id FROM {table} WHERE slug = %s AND is_deleted = FALSE{_scope_sql(entity_type)}",
        [slug] + _scope_params(entity_type, scope_id)
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        f"""
        SELECT r.entity_id
        FROM public.slug_redirects r
        JOIN {table} t ON t.id = r.entity_id AND t.is_deleted = FALSE
        WHERE r.entity_type = %s AND r.scope_id = %s AND r.old_slug = %s
        """,
        [entity_type, scope_id or 0, slug]
    )
    row = cursor.fetchone()

    return row[0] if row else None
