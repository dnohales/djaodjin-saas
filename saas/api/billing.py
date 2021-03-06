# Copyright (c) 2016, DjaoDjin inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Add and remove plans from a user subscription cart.

.. http:post:: /api/cart/

    Add a ``Plan`` into the subscription cart of a ``User``.

   **Example request**:

   .. sourcecode:: http

    {
        "plan": "premium",
    }

   **Example response**:

   .. sourcecode:: http

    {
        "plan": "premium",
    }

.. http:delete:: /api/cart/:plan

    Remove a ``Plan`` from the subscription cart of a ``User``.

   **Example request**:

   .. sourcecode:: http

   **Example response**:

   .. sourcecode:: http

   OK
"""
import logging

from django.core.exceptions import MultipleObjectsReturned
from django.shortcuts import get_object_or_404
from rest_framework.generics import (CreateAPIView, DestroyAPIView,
    ListCreateAPIView)
from rest_framework.response import Response
from rest_framework import serializers, status

from ..backends import ProcessorError
from ..mixins import CartMixin, OrganizationMixin
from ..models import CartItem
from .serializers import (CartItemSerializer, ChargeSerializer,
    InvoicableSerializer)

#pylint: disable=no-init,old-style-class
LOGGER = logging.getLogger(__name__)


class CheckoutSerializer(serializers.Serializer):

    remember_card = serializers.BooleanField(required=False)
    processor_token = serializers.CharField(max_length=255)

    def create(self, validated_data):
        raise RuntimeError('`create()` should not be called.')

    def update(self, instance, validated_data):
        raise RuntimeError('`update()` should not be called.')


class CartItemAPIView(CartMixin, CreateAPIView):
    """
    Add a plan into the cart of the request user.

    **Example request**:

    .. sourcecode:: http

        POST /api/cart/

        {
            "plan": "open-space",
            "nb_periods": 1,
            "first_name": "",
            "last_name": "",
            "email": ""
        }

    **Example response**:

    .. sourcecode:: http

        {
            "plan": "open-space",
            "nb_periods": 1,
            "first_name": "",
            "last_name": "",
            "email": ""
        }
    """
    #pylint: disable=no-member

    model = CartItem
    serializer_class = CartItemSerializer

    # XXX This was a workaround until we figure what is wrong with proxy
    # and csrf, unfortunately it prevents authenticated users to add into
    # their db cart, instead put their choices into the unauth session.
    # authentication_classes = []

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            cart_item, created = self.insert_item(request, **serializer.data)
            # insert_item will either return a dict or a CartItem instance
            # (which cannot be directly serialized).
            if isinstance(cart_item, CartItem):
                cart_item = serializer.to_representation(cart_item)
            if created:
                headers = self.get_success_headers(cart_item)
                return Response(cart_item, status=status.HTTP_201_CREATED,
                    headers=headers)
            else:
                headers = self.get_success_headers(cart_item)
                return Response(cart_item, status=status.HTTP_200_OK,
                    headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CartItemDestroyAPIView(DestroyAPIView):
    """
    Remove a ``Plan`` from the subscription cart of the request user.
    """

    model = CartItem

    @staticmethod
    def destroy_in_session(request, *args, **kwargs):
        #pylint: disable=unused-argument
        cart_items = []
        if request.session.has_key('cart_items'):
            cart_items = request.session['cart_items']
        candidate = kwargs.get('plan')
        serialized_cart_items = []
        found = False
        for item in cart_items:
            if item['plan'] == candidate:
                found = True
                continue
            serialized_cart_items += [item]
        request.session['cart_items'] = serialized_cart_items
        return found

    def get_object(self):
        result = None
        try:
            result = get_object_or_404(CartItem,
                plan__slug=self.kwargs.get('plan'),
                user=self.request.user, recorded=False)
        except MultipleObjectsReturned as err:
            # This should not happen but in case the db is corrupted,
            # we want to do something acceptable to the user.
            LOGGER.exception(err)
            result = CartItem.objects.filter(
                plan__slug=self.kwargs.get('plan'),
                user=self.request.user, recorded=False).first()
        return result

    def delete(self, request, *args, **kwargs):
        destroyed = self.destroy_in_session(request, *args, **kwargs)
        # We found the items in the session cart, nothing else to do.
        if not destroyed and self.request.user.is_authenticated():
            # If the user is authenticated, we delete the cart items
            # from the database.
            return self.destroy(request, *args, **kwargs)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CheckoutAPIView(CartMixin, OrganizationMixin, ListCreateAPIView):
    """
    Get the list of invoicables from a user cart, and checkout the cart
    of the request user on POST.

    **Example request**:

    .. sourcecode:: http

        GET /api/billing/:organization/checkout

    **Example response**:

    .. sourcecode:: http

        [{
          "subscription":{
              "created_at":"2016-06-21T23:24:09.242925Z",
              "ends_at":"2016-10-21T23:24:09.229768Z",
              "description":null,
              "organization":{
                  "slug":"xia",
                  "full_name":"Xia",
                  "printable_name":"Xia",
                  "created_at":"2012-08-14T23:16:55Z",
                  "email":"xia@localhost.localdomain"
              },
              "plan":{
                  "slug":"basic",
                  "title":"Basic",
                  "description":"Basic Plan",
                  "is_active":true,
                  "setup_amount":0,
                  "period_amount":2000,
                  "interval":4,
                  "app_url":"/app/"
              },
              "auto_renew":true
          },
          "lines":[{
              "created_at":"2016-06-21T23:42:13.863739Z",
              "description":"Subscription to basic until 2016/11/21 (1 month)",
              "amount":"$20.00",
              "is_debit":false,
              "orig_account":"Receivable",
              "orig_organization":"cowork",
              "orig_amount":2000,
              "orig_unit":"usd",
              "dest_account":"Payable",
              "dest_organization":"xia",
              "dest_amount":2000,
              "dest_unit":"usd"
          }],
          "options":[]
        }]

    **Example request**:

    .. sourcecode:: http

        POST /api/billing/:organization/checkout

        {
            "remember_card": true,
            "processor_token": "token-from-payment-processor"
        }

    **Example response**:

    .. sourcecode:: http

       {
            "created_at": "2016-06-21T23:42:44.270977Z",
            "processor_key": "pay_5lK5TacFH3gbKe"
            "amount": 2000,
            "unit": "usd",
            "description": "Charge pay_5lK5TacFH3gblP on credit card of Xia",
            "last4": "1234",
            "exp_date": "2016-06-01",
            "state": "created"
        }
    """
    serializer_class = CheckoutSerializer

    def get_queryset(self):
        return super(CheckoutAPIView, self).as_invoicables(
            self.request.user, self.organization)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = InvoicableSerializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):#pylint:disable=unused-argument
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        queryset = self.get_queryset()
        try:
            charge = self.organization.checkout(
                queryset, self.request.user,
                token=serializer.validated_data['processor_token'],
                remember_card=serializer.validated_data.get(
                    'remember_card', False))
            if charge and charge.invoiced_total_amount > 0:
                result = ChargeSerializer(charge)
                return Response(result.data, status=status.HTTP_200_OK)
        except ProcessorError as err:
            return Response({"details": err}, status=status.HTTP_403_FORBIDDEN)
        return Response({}, status=status.HTTP_200_OK)
