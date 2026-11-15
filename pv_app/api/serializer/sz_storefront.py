from decimal import Decimal

from rest_framework import serializers
from rest_framework.fields import empty

from helpers.utils import MAX_VIDEO_UPLOAD_BYTES, VIDEO_CONTENT_TYPES


MAX_REVIEW_IMAGES = 5

# Stored value -> label shown on the review card ("Fit: Perfect").
FIT_FEEDBACK_CHOICES = [
    ("RUNS_SMALL", "Runs Small"),
    ("PERFECT", "Perfect"),
    ("SLIGHTLY_OVERSIZED", "Slightly Oversized"),
    ("RUNS_LARGE", "Runs Large"),
]

FIT_FEEDBACK_LABELS = dict(FIT_FEEDBACK_CHOICES)


# ---------------------------------------------------------------------
# Quantity discount tiers
# ---------------------------------------------------------------------

class CreateQuantityTierSerializer(serializers.Serializer):
    min_quantity = serializers.IntegerField(min_value=2)
    discount_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0.01"), max_value=Decimal("90"))
    is_active = serializers.BooleanField(required=False, default=True)


class UpdateQuantityTierSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    min_quantity = serializers.IntegerField(min_value=2, required=False)
    discount_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0.01"), max_value=Decimal("90"), required=False)
    is_active = serializers.BooleanField(required=False)


# ---------------------------------------------------------------------
# Size charts
# ---------------------------------------------------------------------

class CreateSizeChartSerializer(serializers.Serializer):
    category_id = serializers.IntegerField()
    unit = serializers.ChoiceField(choices=["IN", "CM"], required=False, default="IN")
    headers = serializers.ListField(child=serializers.CharField(max_length=50), min_length=1, max_length=12)
    rows = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField(max_length=50, allow_blank=True), min_length=1, max_length=12),
        min_length=1, max_length=30
    )
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField(required=False, default=True)


class UpdateSizeChartSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    unit = serializers.ChoiceField(choices=["IN", "CM"], required=False)
    headers = serializers.ListField(child=serializers.CharField(max_length=50), min_length=1, max_length=12, required=False)
    rows = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField(max_length=50, allow_blank=True), min_length=1, max_length=12),
        min_length=1, max_length=30, required=False
    )
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField(required=False)


# ---------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------

class WishlistSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)


# ---------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------

class OptionalBooleanField(serializers.BooleanField):
    """DRF reads a BooleanField missing from multipart/form-data as False
    (checkbox semantics). Review requests are multipart when photos are
    attached, so a missing field must stay missing instead."""
    default_empty_html = empty


class CreateReviewSerializer(serializers.Serializer):
    """JSON, or multipart/form-data when photos are attached (files in a
    repeated 'images' field, max 5)."""
    order_item_id = serializers.IntegerField(min_value=1)
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True, allow_null=True)
    review_text = serializers.CharField(max_length=2000, required=False, allow_blank=True, allow_null=True)
    is_recommended = OptionalBooleanField(required=False, default=True)
    fit_feedback = serializers.ChoiceField(choices=FIT_FEEDBACK_CHOICES, required=False, allow_null=True)


class UpdateReviewSerializer(serializers.Serializer):
    """Author: any content field, delete_image_ids, new 'images' files.
    Admin: is_active only (hide / unhide)."""
    id = serializers.IntegerField(min_value=1)
    rating = serializers.IntegerField(min_value=1, max_value=5, required=False)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True, allow_null=True)
    review_text = serializers.CharField(max_length=2000, required=False, allow_blank=True, allow_null=True)
    is_recommended = OptionalBooleanField(required=False)
    fit_feedback = serializers.ChoiceField(choices=FIT_FEEDBACK_CHOICES, required=False, allow_null=True)
    delete_image_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    is_active = OptionalBooleanField(required=False)


# ---------------------------------------------------------------------
# Product videos (direct browser -> RustFS upload)
# ---------------------------------------------------------------------

class VideoUploadUrlSerializer(serializers.Serializer):
    content_type = serializers.ChoiceField(choices=sorted(VIDEO_CONTENT_TYPES))
    file_size = serializers.IntegerField(min_value=1, max_value=MAX_VIDEO_UPLOAD_BYTES)


class AttachProductVideoSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField(min_value=1)
    file_id = serializers.CharField(max_length=255)
    display_order = serializers.IntegerField(min_value=1, required=False)
