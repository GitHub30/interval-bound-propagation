# coding=utf-8
# Copyright 2018 The Interval Bound Propagation Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Definition of input bounds to each layer."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc

from interval_bound_propagation.src import verifiable_wrapper
import sonnet as snt
import tensorflow as tf


class AbstractBounds(object):
  """Abstract bounds class."""

  __metaclass__ = abc.ABCMeta

  def propagate_through(self, wrapper):
    """Propagates bounds through a verifiable wrapper.

    Args:
      wrapper: `verifiable_wrapper.VerifiableWrapper`

    Returns:
      New bounds.
    """
    module = wrapper.module
    if isinstance(wrapper, verifiable_wrapper.LinearFCWrapper):
      w = module.w
      b = module.b if module.has_bias else None
      return self._linear(w, b)
    elif isinstance(wrapper, verifiable_wrapper.LinearConv2dWrapper):
      w = module.w
      b = module.b if module.has_bias else None
      padding = module.padding
      strides = module.stride[1:-1]
      return self._conv2d(w, b, padding, strides)
    elif isinstance(wrapper, verifiable_wrapper.MonotonicWrapper):
      return self._monotonic_fn(module)
    elif isinstance(wrapper, verifiable_wrapper.BatchNormWrapper):
      return self._batch_norm(module.mean, module.variance, module.scale,
                              module.bias, module.epsilon)
    elif isinstance(wrapper, verifiable_wrapper.BatchFlattenWrapper):
      return self._batch_flatten()
    else:
      raise NotImplementedError('{} not supported.'.format(
          wrapper.__class__.__name__))

  @abc.abstractmethod
  def combine_with(self, bounds):
    """Produces new bounds that keep track of multiple input bounds."""

  def _raise_not_implemented(self, name):
    raise NotImplementedError(
        '{} modules are not supported by "{}".'.format(
            name, self.__class__.__name__))

  def _linear(self, w, b):  # pylint: disable=unused-argument
    self._raise_not_implemented('snt.Linear')

  def _conv2d(self, w, b, padding, strides):  # pylint: disable=unused-argument
    self._raise_not_implemented('snt.Conv2D')

  def _monotonic_fn(self, fn):
    self._raise_not_implemented(fn.__name__)

  def _batch_norm(self, mean, variance, scale, bias, epsilon):  # pylint: disable=unused-argument
    self._raise_not_implemented('ibp.BatchNorm')

  def _batch_flatten(self):  # pylint: disable=unused-argument
    self._raise_not_implemented('snt.BatchFlatten')


class IntervalBounds(AbstractBounds):
  """Axis-aligned bounding box."""

  def __init__(self, lower, upper):
    self._lower = lower
    self._upper = upper

  @property
  def lower(self):
    return self._lower

  @property
  def upper(self):
    return self._upper

  def combine_with(self, bounds):
    if not isinstance(bounds, IntervalBounds):
      raise NotImplementedError('Cannot combine IntervalBounds with '
                                '{}'.format(bounds))
    bounds._ensure_singleton()  # pylint: disable=protected-access
    if not isinstance(self._lower, tuple):
      self._ensure_singleton()
      lower = (self._lower, bounds.lower)
      upper = (self._upper, bounds.upper)
    else:
      lower = self._lower + (bounds.lower,)
      upper = self._upper + (bounds.upper,)
    return IntervalBounds(lower, upper)

  def _ensure_singleton(self):
    if isinstance(self._lower, tuple) or isinstance(self._upper, tuple):
      raise ValueError('Cannot proceed with multiple inputs.')

  def _linear(self, w, b):
    self._ensure_singleton()
    c = (self.lower + self.upper) / 2.
    r = (self.upper - self.lower) / 2.
    c = tf.matmul(c, w)
    if b is not None:
      c = c + b
    r = tf.matmul(r, tf.abs(w))
    return IntervalBounds(c - r, c + r)

  def _conv2d(self, w, b, padding, strides):
    self._ensure_singleton()
    c = (self.lower + self.upper) / 2.
    r = (self.upper - self.lower) / 2.
    c = tf.nn.convolution(c, w, padding=padding, strides=strides)
    if b is not None:
      c = c + b
    r = tf.nn.convolution(r, tf.abs(w), padding=padding, strides=strides)
    return IntervalBounds(c - r, c + r)

  def _monotonic_fn(self, fn):
    if isinstance(self._lower, tuple):
      assert isinstance(self._upper, tuple)
      return IntervalBounds(fn(*self.lower),
                            fn(*self.upper))
    self._ensure_singleton()
    return IntervalBounds(fn(self.lower),
                          fn(self.upper))

  def _batch_norm(self, mean, variance, scale, bias, epsilon):
    self._ensure_singleton()
    # Element-wise multiplier.
    multiplier = tf.rsqrt(variance + epsilon)
    if scale is not None:
      multiplier *= scale
    w = multiplier
    # Element-wise bias.
    b = -multiplier * mean
    if bias is not None:
      b += bias
    b = tf.squeeze(b, axis=0)
    # Because the scale might be negative, we need to apply a strategy similar
    # to linear.
    c = (self.lower + self.upper) / 2.
    r = (self.upper - self.lower) / 2.
    c = tf.multiply(c, w) + b
    r = tf.multiply(r, tf.abs(w))
    return IntervalBounds(c - r, c + r)

  def _batch_flatten(self):
    self._ensure_singleton()
    return IntervalBounds(snt.BatchFlatten()(self.lower),
                          snt.BatchFlatten()(self.upper))
