from rest_framework import serializers


class CheckoutOrderSerializer(serializers.Serializer):
    address_id = serializers.IntegerField(min_value=1)
    coupon_code = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)

    # Buy Now: the order is built from just this size + quantity, not the
    # cart. Like any order, a cart line for the same size is cleared once
    # the order is placed (COD) / paid (Razorpay).
    variant_size_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class VerifyPaymentSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField(max_length=64)
    razorpay_payment_id = serializers.CharField(max_length=64)
    razorpay_signature = serializers.CharField(max_length=128)