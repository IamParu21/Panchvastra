from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

from helpers.middleware import user_authentication_required
from helpers.utils import generic_response_handler, validate_image_files
from pv_app.api.business_logic.bl_storefront import (
    ADMIN_ROLE_ID,
    add_to_wishlist,
    attach_product_video,
    create_quantity_tier,
    create_review,
    create_size_chart,
    delete_quantity_tier,
    delete_review,
    delete_size_chart,
    get_quantity_tiers,
    get_reviews,
    get_size_charts,
    get_video_upload_url,
    get_wishlist,
    remove_from_wishlist,
    update_quantity_tier,
    update_review,
    update_size_chart
)
from pv_app.api.serializer.sz_storefront import (
    MAX_REVIEW_IMAGES,
    AttachProductVideoSerializer,
    CreateQuantityTierSerializer,
    CreateReviewSerializer,
    CreateSizeChartSerializer,
    UpdateQuantityTierSerializer,
    UpdateReviewSerializer,
    UpdateSizeChartSerializer,
    VideoUploadUrlSerializer,
    WishlistSerializer
)
from pv_app.api.swagger.swag_storefront import (
    product_reviews_schema,
    product_video_upload_url_schema,
    product_videos_schema,
    quantity_discount_tiers_schema,
    size_charts_schema,
    wishlist_schema
)


FORBIDDEN = ({"message": "You are not authorized to perform this action.", "data": {}}, status.HTTP_403_FORBIDDEN)


def _is_true(value):
    return str(value).lower() in ("1", "true", "yes")


@quantity_discount_tiers_schema
@user_authentication_required(role_required=[1, 2], public_methods=["GET"])
@api_view(["GET", "POST", "PUT", "DELETE"])
@generic_response_handler
def quantity_discount_tiers(request):

    if request.method == "GET":
        return get_quantity_tiers(
            include_inactive=request.role_id == ADMIN_ROLE_ID and _is_true(request.GET.get("include_inactive"))
        )

    if request.role_id != ADMIN_ROLE_ID:
        return FORBIDDEN

    if request.method == "POST":
        serializer = CreateQuantityTierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return create_quantity_tier(serializer.validated_data, request.user_id)

    if request.method == "PUT":
        serializer = UpdateQuantityTierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return update_quantity_tier(serializer.validated_data, request.user_id)

    return delete_quantity_tier(request.GET.get("id"), request.user_id)


@size_charts_schema
@user_authentication_required(role_required=[1, 2], public_methods=["GET"])
@api_view(["GET", "POST", "PUT", "DELETE"])
@generic_response_handler
def size_charts(request):

    if request.method == "GET":
        return get_size_charts(
            chart_id=request.GET.get("id"),
            category_id=request.GET.get("category_id"),
            include_inactive=request.role_id == ADMIN_ROLE_ID and _is_true(request.GET.get("include_inactive"))
        )

    if request.role_id != ADMIN_ROLE_ID:
        return FORBIDDEN

    if request.method == "POST":
        serializer = CreateSizeChartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return create_size_chart(serializer.validated_data, request.user_id)

    if request.method == "PUT":
        serializer = UpdateSizeChartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return update_size_chart(serializer.validated_data, request.user_id)

    return delete_size_chart(request.GET.get("id"), request.user_id)


@wishlist_schema
@user_authentication_required(role_required=[1, 2])
@api_view(["GET", "POST", "DELETE"])
@generic_response_handler
def wishlist(request):

    if request.method == "GET":
        return get_wishlist(
            request.user_id,
            ids_only=_is_true(request.GET.get("ids_only")),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 20)
        )

    if request.method == "POST":
        serializer = WishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return add_to_wishlist(serializer.validated_data["product_id"], request.user_id)

    return remove_from_wishlist(request.GET.get("product_id"), request.user_id)


@product_reviews_schema
@user_authentication_required(role_required=[1, 2], public_methods=["GET"])
@api_view(["GET", "POST", "PUT", "DELETE"])
@parser_classes([JSONParser, MultiPartParser, FormParser])
@generic_response_handler
def product_reviews(request):

    if request.method == "GET":
        return get_reviews(
            viewer_id=request.user_id,
            viewer_role=request.role_id,
            review_id=request.GET.get("id"),
            product_id=request.GET.get("product_id"),
            with_photos=_is_true(request.GET.get("with_photos")),
            sort_by=request.GET.get("sort_by", "recent"),
            include_hidden=_is_true(request.GET.get("include_hidden")),
            page=request.GET.get("page", 1),
            page_size=request.GET.get("page_size", 10)
        )

    if request.method == "DELETE":
        return delete_review(request.GET.get("id"), request.user_id, request.role_id)

    images = validate_image_files(request.FILES.getlist("images"))

    if request.method == "POST":
        serializer = CreateReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if len(images) > MAX_REVIEW_IMAGES:
            return {"message": f"A review can have at most {MAX_REVIEW_IMAGES} photos."}, status.HTTP_400_BAD_REQUEST

        return create_review(serializer.validated_data, images, request.user_id)

    serializer = UpdateReviewSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return update_review(serializer.validated_data, images, request.user_id, request.role_id)


@product_video_upload_url_schema
@user_authentication_required(role_required=[1])
@api_view(["POST"])
@generic_response_handler
def product_video_upload_url(request):
    serializer = VideoUploadUrlSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return get_video_upload_url(serializer.validated_data)


@product_videos_schema
@user_authentication_required(role_required=[1])
@api_view(["POST"])
@generic_response_handler
def product_videos(request):
    serializer = AttachProductVideoSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return attach_product_video(serializer.validated_data, request.user_id)
