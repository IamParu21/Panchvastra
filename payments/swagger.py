from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema_view,
    extend_schema,
)

from .serializers import CheckoutOrderSerializer


checkout_payment_schema = extend_schema_view(
    post=extend_schema(
        tags=["Payments"],
        summary="Create Razorpay Order",
        description=(
            "Creates a Razorpay order for one-time payment. Without variant_size_id the "
            "cart is ordered; with variant_size_id (+ quantity, default 1) only that item "
            "is ordered (Buy Now). The active quantity tier is applied automatically on "
            "the total item count, then the coupon on the remaining amount."
        ),
        request=CheckoutOrderSerializer,
    ),
)


cod_order_schema = extend_schema_view(
    post=extend_schema(
        tags=["Payments"],
        summary="Place Cash on Delivery order",
        description=(
            "Places the order immediately for cash-on-delivery — no payment gateway "
            "involved. Stock is decremented and the cart is cleared as part of the "
            "same request, since there is no later webhook to reconcile them. "
            "Buy Now (variant_size_id + quantity) and the quantity tier discount work "
            "the same as in checkout_payment."
        ),
        request=CheckoutOrderSerializer,
    ),
)