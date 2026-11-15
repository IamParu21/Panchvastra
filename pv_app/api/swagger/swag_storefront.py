from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view

from pv_app.api.serializer.sz_storefront import (
    CreateQuantityTierSerializer,
    CreateReviewSerializer,
    CreateSizeChartSerializer,
    UpdateQuantityTierSerializer,
    UpdateReviewSerializer,
    AttachProductVideoSerializer,
    UpdateSizeChartSerializer,
    VideoUploadUrlSerializer,
    WishlistSerializer
)


def _query(name, type_=OpenApiTypes.STR, required=False, description=None):
    return OpenApiParameter(name=name, type=type_, location=OpenApiParameter.QUERY, required=required, description=description)


quantity_discount_tiers_schema = extend_schema_view(
    get=extend_schema(
        tags=["Quantity Discounts"],
        summary="List quantity discount tiers",
        description=(
            "Public. Active tiers sorted by min_quantity. The same list is also embedded in "
            "the product detail response as quantity_discount_tiers. Admin may pass "
            "include_inactive=true. Tiers apply to the total item count of the cart (or the "
            "Buy Now quantity) and are applied automatically in cart, checkout and COD."
        ),
        parameters=[_query("include_inactive", OpenApiTypes.BOOL)]
    ),
    post=extend_schema(tags=["Quantity Discounts"], summary="Create tier (admin)", request=CreateQuantityTierSerializer),
    put=extend_schema(tags=["Quantity Discounts"], summary="Update tier (admin)", request=UpdateQuantityTierSerializer),
    delete=extend_schema(
        tags=["Quantity Discounts"], summary="Delete tier (admin)",
        parameters=[_query("id", OpenApiTypes.INT, required=True)]
    )
)


size_charts_schema = extend_schema_view(
    get=extend_schema(
        tags=["Size Charts"],
        summary="List or fetch size charts",
        description=(
            "Public. With id or category_id returns one chart; otherwise all charts. The "
            "product detail response already embeds its category's chart as size_chart, so "
            "the product page doesn't need this call."
        ),
        parameters=[
            _query("id", OpenApiTypes.INT),
            _query("category_id", OpenApiTypes.INT),
            _query("include_inactive", OpenApiTypes.BOOL)
        ]
    ),
    post=extend_schema(
        tags=["Size Charts"],
        summary="Create a category's size chart (admin)",
        description="One chart per category. Every row must have exactly one value per header.",
        request=CreateSizeChartSerializer
    ),
    put=extend_schema(tags=["Size Charts"], summary="Update size chart (admin)", request=UpdateSizeChartSerializer),
    delete=extend_schema(
        tags=["Size Charts"], summary="Delete size chart (admin)",
        parameters=[_query("id", OpenApiTypes.INT, required=True)]
    )
)


wishlist_schema = extend_schema_view(
    get=extend_schema(
        tags=["Wishlist"],
        summary="Get wishlist",
        description=(
            "ids_only=true returns just the product ids (use it once per page load to fill "
            "every heart icon and the header count). Otherwise a paginated list of product "
            "cards, newest first. Inactive/deleted products are left out."
        ),
        parameters=[
            _query("ids_only", OpenApiTypes.BOOL),
            _query("page", OpenApiTypes.INT),
            _query("page_size", OpenApiTypes.INT)
        ]
    ),
    post=extend_schema(
        tags=["Wishlist"], summary="Add product to wishlist",
        description="Idempotent: adding an already-saved product returns 200.",
        request=WishlistSerializer
    ),
    delete=extend_schema(
        tags=["Wishlist"], summary="Remove product from wishlist",
        parameters=[_query("product_id", OpenApiTypes.INT, required=True)]
    )
)


product_reviews_schema = extend_schema_view(
    get=extend_schema(
        tags=["Reviews"],
        summary="List reviews of a product, or fetch one",
        description=(
            "Public. With id returns one review. Otherwise product_id is required (admin may "
            "omit it to moderate across products) and the response is "
            "{summary: {average_rating, total_reviews, recommend_percentage, with_photos_count, "
            "distribution[]}, reviews: [...]} plus pagination. with_photos=true is the "
            "'With Photos' tab. Admin may pass include_hidden=true."
        ),
        parameters=[
            _query("id", OpenApiTypes.INT),
            _query("product_id", OpenApiTypes.INT),
            _query("with_photos", OpenApiTypes.BOOL),
            _query("sort_by", description="recent (default) | oldest | highest | lowest"),
            _query("include_hidden", OpenApiTypes.BOOL),
            _query("page", OpenApiTypes.INT),
            _query("page_size", OpenApiTypes.INT)
        ]
    ),
    post=extend_schema(
        tags=["Reviews"],
        summary="Write a review",
        description=(
            "Only for an item of the caller's own DELIVERED order, once per order item "
            "(order detail returns review_id per item; null means not reviewed yet). Send "
            "JSON, or multipart/form-data with up to 5 files in a repeated 'images' field."
        ),
        request=CreateReviewSerializer
    ),
    put=extend_schema(
        tags=["Reviews"],
        summary="Edit a review / hide it (admin)",
        description=(
            "Author: change any content field, remove photos with delete_image_ids, add photos "
            "via multipart 'images' (max 5 in total). Admin: is_active=false hides the review, "
            "true shows it again. Returns the updated review."
        ),
        request=UpdateReviewSerializer
    ),
    delete=extend_schema(
        tags=["Reviews"], summary="Delete a review (author or admin)",
        parameters=[_query("id", OpenApiTypes.INT, required=True)]
    )
)


product_video_upload_url_schema = extend_schema_view(
    post=extend_schema(
        tags=["Product Videos"],
        summary="Get a direct upload URL for a product video (admin)",
        description=(
            "Step 1 of 3. Returns upload_url, headers and file_id. Step 2: the browser sends "
            "the file itself with a PUT to upload_url, with exactly the returned headers "
            "(Content-Type is part of the signature), straight to storage, never through this "
            "API. The URL expires in 15 minutes. Step 3: POST /v1/product_videos/ with the "
            "file_id. MP4, WebM or MOV, max 50 MB."
        ),
        request=VideoUploadUrlSerializer
    )
)


product_videos_schema = extend_schema_view(
    post=extend_schema(
        tags=["Product Videos"],
        summary="Attach an uploaded video to a variant's gallery (admin)",
        description=(
            "Step 3. Checks the uploaded object (exists, issued by this API, real size and type) "
            "and adds it to the variant gallery with media_type VIDEO. display_order is optional "
            "(default: after the last item). To reorder or remove it later use the existing "
            "product update fields variant_image_orders / delete_variant_image_ids with the "
            "returned id."
        ),
        request=AttachProductVideoSerializer
    )
)
