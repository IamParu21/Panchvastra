import json
import math
import traceback

from django.db import IntegrityError, connection, transaction
from rest_framework import status

from helpers.utils import (
    MAX_VIDEO_UPLOAD_BYTES,
    VIDEO_CONTENT_TYPES,
    VIDEO_UPLOAD_PREFIX,
    clamp_page,
    clamp_page_size,
    create_video_upload_url,
    delete_image_from_storage,
    get_storage_object_info,
    resolve_pagination,
    storage_public_url,
    upload_image_to_storage
)
from pv_app.api.serializer.sz_storefront import FIT_FEEDBACK_LABELS, MAX_REVIEW_IMAGES


ADMIN_ROLE_ID = 1


def _decode_json(value):
    # psycopg2 returns json/jsonb built in SQL as text here (no adapter is
    # registered), same as the rest of the codebase handles it.
    return json.loads(value) if isinstance(value, str) else value


def _delete_files_quietly(file_ids):
    """Best-effort storage cleanup. Never raises: a leftover object in the
    bucket is harmless, failing the request over it is not."""
    for file_id in file_ids:
        try:
            delete_image_from_storage(file_id)
        except Exception:
            traceback.print_exc()


# =====================================================================
# Quantity discount tiers ("More you shop, more you save")
# =====================================================================

def fetch_active_quantity_tiers(cursor):
    cursor.execute("""
        SELECT min_quantity, discount_percentage
        FROM public.quantity_discount_tiers
        WHERE is_active = TRUE AND is_deleted = FALSE
        ORDER BY min_quantity
    """)

    return [
        {"min_quantity": min_quantity, "discount_percentage": float(percentage)}
        for min_quantity, percentage in cursor.fetchall()
    ]


def resolve_quantity_discount(tiers, item_count, amount):
    """Highest tier the whole-cart item_count qualifies for, applied to
    amount. tiers must be sorted by min_quantity ascending."""
    applied_tier = None
    next_tier = None

    for tier in tiers:
        if item_count >= tier["min_quantity"]:
            applied_tier = tier
        elif next_tier is None:
            next_tier = tier

    discount_amount = (
        round(amount * applied_tier["discount_percentage"] / 100, 2)
        if applied_tier else 0.0
    )

    return {
        "discount_amount": discount_amount,
        "applied_tier": applied_tier,
        "next_tier": (
            {**next_tier, "items_needed": next_tier["min_quantity"] - item_count}
            if next_tier else None
        )
    }


QUANTITY_TIER_COLUMNS = ["id", "min_quantity", "discount_percentage", "is_active", "created_at", "updated_at"]


def get_quantity_tiers(include_inactive=False):
    where = "is_deleted = FALSE" + ("" if include_inactive else " AND is_active = TRUE")

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT {", ".join(QUANTITY_TIER_COLUMNS)}
            FROM public.quantity_discount_tiers
            WHERE {where}
            ORDER BY min_quantity
        """)
        rows = cursor.fetchall()

    data = []
    for row in rows:
        item = dict(zip(QUANTITY_TIER_COLUMNS, row))
        item["discount_percentage"] = float(item["discount_percentage"])
        data.append(item)

    return {"message": "Data fetched successfully.", "data": data}, status.HTTP_200_OK


def create_quantity_tier(data, user_id):
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.quantity_discount_tiers
                    (min_quantity, discount_percentage, is_active, created_by, updated_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
            """, [data["min_quantity"], data["discount_percentage"], data.get("is_active", True), user_id, user_id])
            tier_id = cursor.fetchone()[0]
    except IntegrityError:
        return {"message": f"A tier for {data['min_quantity']} items already exists."}, status.HTTP_409_CONFLICT

    return {"message": "Tier created successfully.", "data": {"id": tier_id}}, status.HTTP_201_CREATED


def update_quantity_tier(data, user_id):
    fields = [f for f in ("min_quantity", "discount_percentage", "is_active") if f in data]

    if not fields:
        return {"message": "Nothing to update."}, status.HTTP_400_BAD_REQUEST

    set_clause = ", ".join(f"{f} = %s" for f in fields)
    params = [data[f] for f in fields] + [user_id, data["id"]]

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"""
                UPDATE public.quantity_discount_tiers
                SET {set_clause}, updated_by = %s, updated_at = NOW()
                WHERE id = %s AND is_deleted = FALSE
                RETURNING id
            """, params)
            row = cursor.fetchone()
    except IntegrityError:
        return {"message": f"A tier for {data['min_quantity']} items already exists."}, status.HTTP_409_CONFLICT

    if not row:
        return {"message": "Tier not found."}, status.HTTP_404_NOT_FOUND

    return {"message": "Tier updated successfully."}, status.HTTP_200_OK


def delete_quantity_tier(tier_id, user_id):
    if not tier_id:
        return {"message": "Tier id is required."}, status.HTTP_400_BAD_REQUEST

    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE public.quantity_discount_tiers
            SET is_deleted = TRUE, is_active = FALSE, updated_by = %s, updated_at = NOW()
            WHERE id = %s AND is_deleted = FALSE
            RETURNING id
        """, [user_id, tier_id])
        row = cursor.fetchone()

    if not row:
        return {"message": "Tier not found."}, status.HTTP_404_NOT_FOUND

    return {"message": "Tier deleted successfully."}, status.HTTP_200_OK


# =====================================================================
# Size charts (one per category)
# =====================================================================

SIZE_CHART_COLUMNS = [
    "id", "category_id", "category_name", "unit", "headers", "rows",
    "notes", "is_active", "created_at", "updated_at"
]


def get_size_charts(chart_id=None, category_id=None, include_inactive=False):
    conditions = ["sc.is_deleted = FALSE"]
    params = []

    if not include_inactive:
        conditions.append("sc.is_active = TRUE")
    if chart_id:
        conditions.append("sc.id = %s")
        params.append(chart_id)
    if category_id:
        conditions.append("sc.category_id = %s")
        params.append(category_id)

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                sc.id, sc.category_id, c.name, sc.unit, sc.chart_headers, sc.chart_rows,
                sc.notes, sc.is_active, sc.created_at, sc.updated_at
            FROM public.size_charts sc
            JOIN public.categories c ON c.id = sc.category_id
            WHERE {" AND ".join(conditions)}
            ORDER BY c.name
        """, params)
        rows = cursor.fetchall()

    data = []
    for row in rows:
        item = dict(zip(SIZE_CHART_COLUMNS, row))
        item["headers"] = _decode_json(item["headers"])
        item["rows"] = _decode_json(item["rows"])
        data.append(item)

    if chart_id or category_id:
        if not data:
            return {"message": "Size chart not found.", "data": {}}, status.HTTP_404_NOT_FOUND
        return {"message": "Data fetched successfully.", "data": data[0]}, status.HTTP_200_OK

    return {"message": "Data fetched successfully.", "data": data}, status.HTTP_200_OK


def _rows_match_headers(headers, rows):
    return all(len(row) == len(headers) for row in rows)


def create_size_chart(data, user_id):
    if not _rows_match_headers(data["headers"], data["rows"]):
        return {"message": "Every row must have one value per header."}, status.HTTP_400_BAD_REQUEST

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.size_charts
                    (category_id, unit, chart_headers, chart_rows, notes, is_active,
                     created_by, updated_by, created_at, updated_at)
                SELECT c.id, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, NOW(), NOW()
                FROM public.categories c
                WHERE c.id = %s AND c.is_deleted = FALSE
                RETURNING id
            """, [
                data.get("unit", "IN"), json.dumps(data["headers"]), json.dumps(data["rows"]),
                data.get("notes"), data.get("is_active", True), user_id, user_id, data["category_id"]
            ])
            row = cursor.fetchone()
    except IntegrityError:
        return {"message": "This category already has a size chart. Update it instead."}, status.HTTP_409_CONFLICT

    if not row:
        return {"message": "Category not found."}, status.HTTP_404_NOT_FOUND

    return {"message": "Size chart created successfully.", "data": {"id": row[0]}}, status.HTTP_201_CREATED


def update_size_chart(data, user_id):
    chart_id = data["id"]

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("""
            SELECT chart_headers, chart_rows
            FROM public.size_charts
            WHERE id = %s AND is_deleted = FALSE
            FOR UPDATE
        """, [chart_id])
        row = cursor.fetchone()

        if not row:
            return {"message": "Size chart not found."}, status.HTTP_404_NOT_FOUND

        headers = data.get("headers", _decode_json(row[0]))
        rows = data.get("rows", _decode_json(row[1]))

        if not _rows_match_headers(headers, rows):
            return {"message": "Every row must have one value per header."}, status.HTTP_400_BAD_REQUEST

        set_parts = []
        params = []

        for field, column in (("unit", "unit"), ("notes", "notes"), ("is_active", "is_active")):
            if field in data:
                set_parts.append(f"{column} = %s")
                params.append(data[field])

        if "headers" in data:
            set_parts.append("chart_headers = %s::jsonb")
            params.append(json.dumps(headers))
        if "rows" in data:
            set_parts.append("chart_rows = %s::jsonb")
            params.append(json.dumps(rows))

        if not set_parts:
            return {"message": "Nothing to update."}, status.HTTP_400_BAD_REQUEST

        cursor.execute(f"""
            UPDATE public.size_charts
            SET {", ".join(set_parts)}, updated_by = %s, updated_at = NOW()
            WHERE id = %s
        """, params + [user_id, chart_id])

    return {"message": "Size chart updated successfully."}, status.HTTP_200_OK


def delete_size_chart(chart_id, user_id):
    if not chart_id:
        return {"message": "Size chart id is required."}, status.HTTP_400_BAD_REQUEST

    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE public.size_charts
            SET is_deleted = TRUE, is_active = FALSE, updated_by = %s, updated_at = NOW()
            WHERE id = %s AND is_deleted = FALSE
            RETURNING id
        """, [user_id, chart_id])
        row = cursor.fetchone()

    if not row:
        return {"message": "Size chart not found."}, status.HTTP_404_NOT_FOUND

    return {"message": "Size chart deleted successfully."}, status.HTTP_200_OK


# =====================================================================
# Wishlist
# =====================================================================

# Wishlist rows whose product is still live; a deactivated product simply
# drops out of the list instead of showing a dead card.
_LIVE_WISHLIST_JOIN = """
    FROM public.wishlist_items w
    JOIN public.products p
        ON p.id = w.product_id AND p.is_deleted = FALSE AND p.is_active = TRUE
"""


def get_wishlist(user_id, ids_only=False, page=1, page_size=20):

    if ids_only:
        # Cheap call for painting every heart on a page (header count,
        # product page, product cards) without fetching card data.
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT w.product_id {_LIVE_WISHLIST_JOIN} WHERE w.user_id = %s", [user_id])
            ids = [row[0] for row in cursor.fetchall()]

        return {"message": "Data fetched successfully.", "data": ids}, status.HTTP_200_OK

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) {_LIVE_WISHLIST_JOIN} WHERE w.user_id = %s", [user_id])
        total_records = cursor.fetchone()[0]

        page, page_size, offset, pagination = resolve_pagination(page, clamp_page_size(page_size, default=20), total_records)

        cursor.execute(f"""
            SELECT
                p.id, p.name, p.slug, w.created_at AS added_at,
                v.id AS variant_id, v.color, v.mrp, v.selling_price,
                (
                    SELECT img.image_url FROM public.product_variant_images img
                    WHERE img.variant_id = v.id AND img.is_deleted = FALSE AND img.is_active = TRUE
                    AND img.media_type = 'IMAGE'
                    ORDER BY img.display_order ASC LIMIT 1
                ) AS primary_image
            {_LIVE_WISHLIST_JOIN}
            LEFT JOIN LATERAL (
                SELECT pv.id, pv.color, pv.mrp, pv.selling_price
                FROM public.product_variants pv
                WHERE pv.product_id = p.id AND pv.is_deleted = FALSE AND pv.is_active = TRUE
                ORDER BY pv.is_default DESC, pv.display_order ASC NULLS LAST, pv.selling_price ASC
                LIMIT 1
            ) v ON TRUE
            WHERE w.user_id = %s
            ORDER BY w.created_at DESC
            LIMIT %s OFFSET %s
        """, [user_id, page_size, offset])
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    data = []
    for row in rows:
        item = dict(zip(columns, row))
        mrp = float(item["mrp"] or 0)
        selling_price = float(item["selling_price"] or 0)
        item["mrp"] = mrp
        item["selling_price"] = selling_price
        item["discount_percentage"] = round((mrp - selling_price) / mrp * 100) if mrp > 0 else 0
        data.append(item)

    return {"message": "Data fetched successfully.", "data": data, "pagination": pagination}, status.HTTP_200_OK


def add_to_wishlist(product_id, user_id):
    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO public.wishlist_items (user_id, product_id, created_at)
            SELECT %s, p.id, NOW()
            FROM public.products p
            WHERE p.id = %s AND p.is_deleted = FALSE AND p.is_active = TRUE
            ON CONFLICT (user_id, product_id) DO NOTHING
            RETURNING id
        """, [user_id, product_id])

        if cursor.fetchone():
            return {"message": "Added to wishlist."}, status.HTTP_201_CREATED

        # Nothing inserted: either already there, or the product isn't live.
        cursor.execute("""
            SELECT 1 FROM public.wishlist_items WHERE user_id = %s AND product_id = %s
        """, [user_id, product_id])

        if cursor.fetchone():
            return {"message": "Already in wishlist."}, status.HTTP_200_OK

    return {"message": "Product not found."}, status.HTTP_404_NOT_FOUND


def remove_from_wishlist(product_id, user_id):
    if not product_id:
        return {"message": "product_id is required."}, status.HTTP_400_BAD_REQUEST

    with connection.cursor() as cursor:
        cursor.execute("""
            DELETE FROM public.wishlist_items
            WHERE user_id = %s AND product_id = %s
            RETURNING id
        """, [user_id, product_id])
        row = cursor.fetchone()

    if not row:
        return {"message": "Product is not in your wishlist."}, status.HTTP_404_NOT_FOUND

    return {"message": "Removed from wishlist."}, status.HTTP_200_OK


# =====================================================================
# Reviews
# =====================================================================

REVIEW_SORTS = {
    "recent": "r.created_at DESC, r.id DESC",
    "oldest": "r.created_at ASC, r.id ASC",
    "highest": "r.rating DESC, r.created_at DESC",
    "lowest": "r.rating ASC, r.created_at DESC",
}

_REVIEW_SELECT = """
    SELECT
        r.id, r.product_id, r.rating, r.title, r.review_text, r.is_recommended,
        r.fit_feedback, r.is_active, r.created_at, r.updated_at,
        r.user_id, u.first_name, u.last_name,
        oi.size, oi.color,
        (
            SELECT COALESCE(json_agg(json_build_object(
                'id', ri.id, 'image_url', ri.image_url
            ) ORDER BY ri.display_order, ri.id), '[]')
            FROM public.product_review_images ri
            WHERE ri.review_id = r.id
        ) AS images
    FROM public.product_reviews r
    JOIN public.users u ON u.id = r.user_id
    JOIN public.order_items oi ON oi.id = r.order_item_id
"""

_HAS_IMAGES_SQL = "EXISTS (SELECT 1 FROM public.product_review_images ri WHERE ri.review_id = r.id)"


def _serialize_review(columns, row, viewer_id=None):
    item = dict(zip(columns, row))

    first_name = (item.pop("first_name") or "").strip()
    last_name = (item.pop("last_name") or "").strip()
    # "Rohit S." — never expose a customer's full surname publicly.
    item["reviewer_name"] = f"{first_name} {last_name[0]}." if last_name else (first_name or "Customer")

    item["fit_feedback_label"] = FIT_FEEDBACK_LABELS.get(item["fit_feedback"])
    item["is_verified_purchase"] = True  # every review is tied to a delivered order item
    item["images"] = _decode_json(item["images"])
    item["is_mine"] = viewer_id is not None and item["user_id"] == viewer_id
    item.pop("user_id")

    return item


def get_reviews(
    viewer_id=None,
    viewer_role=None,
    review_id=None,
    product_id=None,
    with_photos=False,
    sort_by="recent",
    include_hidden=False,
    page=1,
    page_size=10
):
    is_admin = viewer_role == ADMIN_ROLE_ID

    # ---- single review (e.g. to prefill the edit form) -------------------
    if review_id:
        with connection.cursor() as cursor:
            cursor.execute(_REVIEW_SELECT + " WHERE r.id = %s AND r.is_deleted = FALSE", [review_id])
            row = cursor.fetchone()
            columns = [col[0] for col in cursor.description]

        if not row:
            return {"message": "Review not found.", "data": {}}, status.HTTP_404_NOT_FOUND

        review = _serialize_review(columns, row, viewer_id)

        # A hidden review is visible only to its author and admins.
        if not review["is_active"] and not (is_admin or review["is_mine"]):
            return {"message": "Review not found.", "data": {}}, status.HTTP_404_NOT_FOUND

        return {"message": "Data fetched successfully.", "data": review}, status.HTTP_200_OK

    if not product_id and not is_admin:
        return {"message": "product_id is required."}, status.HTTP_400_BAD_REQUEST

    conditions = ["r.is_deleted = FALSE"]
    params = []

    if not (is_admin and include_hidden):
        conditions.append("r.is_active = TRUE")
    if product_id:
        conditions.append("r.product_id = %s")
        params.append(product_id)

    where_clause = " AND ".join(conditions)
    page = clamp_page(page)
    page_size = clamp_page_size(page_size, default=10)

    with connection.cursor() as cursor:

        # One pass gives the summary block, the "With Photos (N)" tab count
        # and the total used for pagination.
        cursor.execute(f"""
            SELECT
                COUNT(*),
                ROUND(AVG(r.rating)::numeric, 1),
                COUNT(*) FILTER (WHERE r.rating = 5),
                COUNT(*) FILTER (WHERE r.rating = 4),
                COUNT(*) FILTER (WHERE r.rating = 3),
                COUNT(*) FILTER (WHERE r.rating = 2),
                COUNT(*) FILTER (WHERE r.rating = 1),
                COUNT(*) FILTER (WHERE r.is_recommended),
                COUNT(*) FILTER (WHERE {_HAS_IMAGES_SQL})
            FROM public.product_reviews r
            WHERE {where_clause}
        """, params)
        (total, average, r5, r4, r3, r2, r1, recommended, with_photos_count) = cursor.fetchone()

        def pct(count):
            return round(count * 100 / total) if total else 0

        summary = {
            "average_rating": float(average or 0),
            "total_reviews": total,
            "recommend_percentage": pct(recommended),
            "with_photos_count": with_photos_count,
            "distribution": [
                {"rating": rating, "count": count, "percentage": pct(count)}
                for rating, count in ((5, r5), (4, r4), (3, r3), (2, r2), (1, r1))
            ]
        }

        list_total = with_photos_count if with_photos else total
        list_where = where_clause + (f" AND {_HAS_IMAGES_SQL}" if with_photos else "")
        order_by = REVIEW_SORTS.get(sort_by, REVIEW_SORTS["recent"])
        offset = (page - 1) * page_size

        reviews = []

        if offset < list_total:
            cursor.execute(
                _REVIEW_SELECT + f" WHERE {list_where} ORDER BY {order_by} LIMIT %s OFFSET %s",
                params + [page_size, offset]
            )
            columns = [col[0] for col in cursor.description]
            reviews = [_serialize_review(columns, row, viewer_id) for row in cursor.fetchall()]

    total_pages = math.ceil(list_total / page_size)

    return {
        "message": "Data fetched successfully.",
        "data": {"summary": summary, "reviews": reviews},
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": list_total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1
        }
    }, status.HTTP_200_OK


def _insert_review_images(cursor, review_id, uploaded, start_order):
    if not uploaded:
        return

    cursor.executemany("""
        INSERT INTO public.product_review_images (review_id, image_url, file_id, display_order, created_at)
        VALUES (%s, %s, %s, %s, NOW())
    """, [
        (review_id, upload["url"], upload["file_id"], start_order + index)
        for index, upload in enumerate(uploaded, start=1)
    ])


def create_review(data, images, user_id):
    order_item_id = data["order_item_id"]

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT oi.product_id, pr.id
            FROM public.order_items oi
            JOIN public.orders o ON o.id = oi.order_id
            LEFT JOIN public.product_reviews pr
                ON pr.order_item_id = oi.id AND pr.is_deleted = FALSE
            WHERE oi.id = %s
            AND o.user_id = %s
            AND o.order_status = 'DELIVERED'
            AND oi.is_deleted = FALSE
            AND o.is_deleted = FALSE
        """, [order_item_id, user_id])
        row = cursor.fetchone()

    if not row:
        return {"message": "You can review only items from your delivered orders."}, status.HTTP_403_FORBIDDEN

    product_id, existing_review_id = row

    if existing_review_id:
        return {
            "message": "You have already reviewed this item. Edit your review instead.",
            "data": {"review_id": existing_review_id}
        }, status.HTTP_409_CONFLICT

    # Upload outside the DB transaction so a slow upload never holds it open;
    # on any DB failure the uploaded files are removed again.
    uploaded = [upload_image_to_storage(image=image, folder="/reviews") for image in images]

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.product_reviews (
                    product_id, user_id, order_item_id, rating, title, review_text,
                    is_recommended, fit_feedback, updated_by, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
            """, [
                product_id, user_id, order_item_id, data["rating"], data.get("title"),
                data.get("review_text"), data.get("is_recommended", True),
                data.get("fit_feedback"), user_id
            ])
            review_id = cursor.fetchone()[0]

            _insert_review_images(cursor, review_id, uploaded, 0)

    except IntegrityError:
        # Two submissions for the same item raced past the check above.
        _delete_files_quietly([u["file_id"] for u in uploaded])
        return {"message": "You have already reviewed this item."}, status.HTTP_409_CONFLICT

    except Exception:
        _delete_files_quietly([u["file_id"] for u in uploaded])
        raise

    return {"message": "Review submitted successfully.", "data": {"id": review_id}}, status.HTTP_201_CREATED


REVIEW_CONTENT_FIELDS = ("rating", "title", "review_text", "is_recommended", "fit_feedback")


def update_review(data, new_images, user_id, role_id):
    review_id = data["id"]
    is_admin = role_id == ADMIN_ROLE_ID
    content_fields = [f for f in REVIEW_CONTENT_FIELDS if f in data]
    delete_image_ids = data.get("delete_image_ids") or []
    touches_content = bool(content_fields or delete_image_ids or new_images)

    if not touches_content and "is_active" not in data:
        return {"message": "Nothing to update."}, status.HTTP_400_BAD_REQUEST

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT r.user_id,
                   (SELECT COUNT(*) FROM public.product_review_images ri WHERE ri.review_id = r.id),
                   (SELECT COALESCE(MAX(display_order), 0) FROM public.product_review_images ri WHERE ri.review_id = r.id),
                   (SELECT COALESCE(array_agg(ri.id), '{}') FROM public.product_review_images ri
                    WHERE ri.review_id = r.id AND ri.id = ANY(%s))
            FROM public.product_reviews r
            WHERE r.id = %s AND r.is_deleted = FALSE
        """, [delete_image_ids, review_id])
        row = cursor.fetchone()

    if not row:
        return {"message": "Review not found."}, status.HTTP_404_NOT_FOUND

    owner_id, image_count, max_order, deletable_ids = row

    if "is_active" in data and not is_admin:
        return {"message": "Only admin can hide or unhide a review."}, status.HTTP_403_FORBIDDEN

    if touches_content and owner_id != user_id:
        return {"message": "You can edit only your own review."}, status.HTTP_403_FORBIDDEN

    invalid_ids = sorted(set(delete_image_ids) - set(deletable_ids))
    if invalid_ids:
        return {"message": "Invalid delete_image_ids.", "data": {"invalid_image_ids": invalid_ids}}, status.HTTP_400_BAD_REQUEST

    if image_count - len(deletable_ids) + len(new_images) > MAX_REVIEW_IMAGES:
        return {"message": f"A review can have at most {MAX_REVIEW_IMAGES} photos."}, status.HTTP_400_BAD_REQUEST

    uploaded = [upload_image_to_storage(image=image, folder="/reviews") for image in new_images]
    removed_file_ids = []

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            set_fields = content_fields + (["is_active"] if "is_active" in data else [])

            set_clause = "".join(f"{field} = %s, " for field in set_fields)
            cursor.execute(f"""
                UPDATE public.product_reviews
                SET {set_clause}updated_by = %s, updated_at = NOW()
                WHERE id = %s
            """, [data[field] for field in set_fields] + [user_id, review_id])

            if deletable_ids:
                cursor.execute("""
                    DELETE FROM public.product_review_images
                    WHERE review_id = %s AND id = ANY(%s)
                    RETURNING file_id
                """, [review_id, list(deletable_ids)])
                removed_file_ids = [r[0] for r in cursor.fetchall() if r[0]]

            _insert_review_images(cursor, review_id, uploaded, max_order)

    except Exception:
        _delete_files_quietly([u["file_id"] for u in uploaded])
        raise

    # Only after the commit, so a rollback never leaves rows pointing at
    # deleted files.
    _delete_files_quietly(removed_file_ids)

    return get_reviews(viewer_id=user_id, viewer_role=role_id, review_id=review_id)


def delete_review(review_id, user_id, role_id):
    if not review_id:
        return {"message": "Review id is required."}, status.HTTP_400_BAD_REQUEST

    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE public.product_reviews
            SET is_deleted = TRUE, is_active = FALSE, updated_by = %s, updated_at = NOW()
            WHERE id = %s AND is_deleted = FALSE AND (user_id = %s OR %s)
            RETURNING id
        """, [user_id, review_id, user_id, role_id == ADMIN_ROLE_ID])
        row = cursor.fetchone()

    if not row:
        return {"message": "Review not found."}, status.HTTP_404_NOT_FOUND

    return {"message": "Review deleted successfully."}, status.HTTP_200_OK


# =====================================================================
# Product videos — direct browser -> RustFS upload, then attach
# =====================================================================
# 1. get_video_upload_url: admin asks for a presigned PUT URL.
# 2. The admin browser PUTs the file straight to RustFS (no app timeout).
# 3. attach_product_video: the app checks the stored object (exists, is
#    one of ours, real size/type) and adds it to the variant's gallery as a
#    product_variant_images row with media_type 'VIDEO'.
# Reorder / remove a video with the existing product update fields
# (variant_image_orders / delete_variant_image_ids) — it is a gallery row
# like any image.

def get_video_upload_url(data):
    return {
        "message": "Upload URL created. PUT the file to upload_url with the given headers, then attach it.",
        "data": create_video_upload_url(data["content_type"])
    }, status.HTTP_200_OK


def attach_product_video(data, user_id):
    file_id = data["file_id"].strip()
    name = file_id[len(VIDEO_UPLOAD_PREFIX):]

    # Only keys this app issued: products/videos/<hex>.<ext>, nothing nested.
    if not file_id.startswith(VIDEO_UPLOAD_PREFIX) or not name or "/" in name or ".." in name:
        return {"message": "Invalid file_id."}, status.HTTP_400_BAD_REQUEST

    # Storage call happens before any DB transaction is opened.
    info = get_storage_object_info(file_id)

    if info is None:
        return {"message": "Video not found in storage. Upload it with the upload URL first."}, status.HTTP_400_BAD_REQUEST

    size, content_type = info

    # The presigned URL can't enforce size, so the real object is checked
    # here; a rejected upload is removed instead of left in the bucket.
    if content_type not in VIDEO_CONTENT_TYPES or size > MAX_VIDEO_UPLOAD_BYTES:
        _delete_files_quietly([file_id])
        return {
            "message": f"Video must be MP4, WebM or MOV and at most {MAX_VIDEO_UPLOAD_BYTES // (1024 * 1024)} MB."
        }, status.HTTP_400_BAD_REQUEST

    video_url = storage_public_url(file_id)

    with transaction.atomic(), connection.cursor() as cursor:

        # Row lock on the variant serializes concurrent attaches, so two
        # videos can't both take the same "next" display_order.
        cursor.execute("""
            SELECT pv.id
            FROM public.product_variants pv
            JOIN public.products p ON p.id = pv.product_id
            WHERE pv.id = %s AND pv.is_deleted = FALSE AND p.is_deleted = FALSE
            FOR UPDATE OF pv
        """, [data["variant_id"]])

        if not cursor.fetchone():
            return {"message": "Variant not found."}, status.HTTP_404_NOT_FOUND

        cursor.execute("""
            SELECT 1 FROM public.product_variant_images
            WHERE image_url = %s AND is_deleted = FALSE
        """, [video_url])

        if cursor.fetchone():
            return {"message": "This video is already attached."}, status.HTTP_409_CONFLICT

        display_order = data.get("display_order")

        if display_order:
            cursor.execute("""
                SELECT 1 FROM public.product_variant_images
                WHERE variant_id = %s AND display_order = %s AND is_deleted = FALSE
            """, [data["variant_id"], display_order])

            if cursor.fetchone():
                return {"message": f"Duplicate display_order {display_order} for this variant."}, status.HTTP_400_BAD_REQUEST
        else:
            cursor.execute("""
                SELECT COALESCE(MAX(display_order), 0) + 1
                FROM public.product_variant_images
                WHERE variant_id = %s AND is_deleted = FALSE
            """, [data["variant_id"]])
            display_order = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO public.product_variant_images
                (variant_id, image_url, media_type, display_order, is_active,
                 created_by, updated_by, created_at, updated_at)
            VALUES (%s, %s, 'VIDEO', %s, TRUE, %s, %s, NOW(), NOW())
            RETURNING id
        """, [data["variant_id"], video_url, display_order, user_id, user_id])

        media_id = cursor.fetchone()[0]

    return {
        "message": "Video attached successfully.",
        "data": {
            "id": media_id,
            "variant_id": data["variant_id"],
            "image_url": video_url,
            "media_type": "VIDEO",
            "display_order": display_order
        }
    }, status.HTTP_201_CREATED
