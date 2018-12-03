# Copyright 2018, The TensorFlow Federated Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""EncodingStageInterface, its adaptive extension, and their implementations.

The interfaces are designed to support encoding and decoding that may happen
in different locations, including possibly different TensorFlow `Session`
objects, without the implementer needing to understand how any communication is
realized. Example scenarios include
* Both encoding and decoding can happen in the same location, such as for
  experimental evaluation of efficiency, and no communication is necessary.
* Both encoding and decoding can happen in different locations, but run in the
  same `Session`, such as distributed datacenter training. The communication
  between locations is handled by TensorFlow.
* Encoding and decoding can happen on multiple locations, and communication
  between them needs to happen outside of `TensorFlow`, such as encoding the
  state of a model which is sent to a mobile device to be later decoded and used
  for inference.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc
import enum
import six


class StateAggregationMode(enum.Enum):
  """Enum of available modes of aggregation for state.

  This enum serves as a declaration of how the `state_update_tensors` returned
  by the `encode` method of `StatefulEncodingStageInterface` should be
  aggregated, before being passed to the `update_state` method.

  This is primarily relevant for the setting where the encoding happens in
  multiple locations, and a function of the encoded objects needs to be computed
  at a central node. The implementation of these modes can differ depending on
  the context. For instance, aggregation of these values in a star topology will
  look differently from a multi-tier aggregation, which needs to know how some
  intermediary representations is to be merged.

  List of available values:
  * `SUM`: Summation.
  * `MIN`: Minimum.
  * `MAX`: Maximum.
  * `CONCAT`: Concatenation. This can necessary for computing arbitrary function
    of a collection of those values, such as a percentile.
  """
  SUM = 1
  MIN = 2
  MAX = 3
  CONCAT = 4


class EncodingStageInterface(six.with_metaclass(abc.ABCMeta)):
  """Interface for the core of encoding logic.

  This core interface should support encoding being executed in a variety of
  contexts. For instance,
  * Both encoding and decoding can happen in the same location, such as for
    experimental evaluation of efficiency.
  * Both encoding and decoding can happen in different locations, but run in the
    same `Session`, such as distributed datacenter training.
  * Encoding and decoding can happen in multiple locations, and communication
    between them needs to happen outside of `TensorFlow`, such as compressing
    a state of a model which is sent to a mobile device to be later used for
    inference.

  This interface is designed such that its implementer need not worry about the
  potential communication patterns, and the implementation will support all.

  Each implementation of this interface is supposed to be a relatively
  elementary transformation. In particular, it does not need to realize any
  representation savings by itself. Instead, a particular compositions of these
  elementary transformations will realize the desired savings. These
  compositions are realized by the `Encoder` class.

  For an adaptive version with a broader interface, see
  `AdaptiveEncodingStageInterface`.
  """

  @abc.abstractproperty
  def compressible_tensors_keys(self):
    """Keys of encoded tensors allowed to be further encoded.

    These keys correspond to tensors in object returned by the `encode` method,
    that are allowed to be further lossily compressed.

    This property does not directly impact the functionality, but is used by the
    `Encoder` class to validate composition.
    """

  @abc.abstractproperty
  def commutes_with_sum(self):
    """`True/False` based on whether the encoding commutes with sum.

    If `True`, it means that given multiple inputs `x` with the same `shape` and
    `dtype`, and the same `params` argument of the `encode` method, the
    implementation is such that every value in the returned `encoded_tensors`
    can be first summed, before being passed to the decoding functionality, and
    the output should be identical (up to numerical precision) to summing the
    fully decoded `Tensor` objects.

    Note that this also assumes that each of the `decode` methods would be used
    with the same values of `decode_params`.

    Returns:
      A boolean, `True` if the encoding commutes with sum.
    """

  @abc.abstractproperty
  def decode_needs_input_shape(self):
    """Whether original shape of the encoded object is needed for decoding.

    If `True`, it means that the `shape` of the `x` argument to the `encode`
    method needs to be provided to the `decode` method. For instance, this is
    needed for bitpacking, where inputs of multiple shapes can result in
    identical bitpacked representations.

    This property will be used by `Encoder` to efficiently realize the
    composition of implementations of this interface.
    """

  @abc.abstractmethod
  def get_params(self, name=None):
    """Returns the parameters needed for encoding.

    This method returns parameters controlling the behavior of the `encode` and
    `decode` methods.

    Implementation of this method should clearly document what are the keys of
    parameters returned by this method, in order for a potential stateful
    subclass being able to adaptively modify only existing keys.

    Note that this method is not purely functional in terms of `TensorFlow`. The
    params can be derived from an internal state of the compressor. For
    instance, if a constructor optionally takes a `Variable` as an input
    argument, which is allowed to change during iterative execution, that
    `Variable`, or a function of it, would be exposed via this method.

    Args:
      name: `string`, name of the operation.

    Returns:
      A tuple `(encode_params, decode_params)`, where
      `encode_params`: A dictionary to be passed as argument to the `encode`
        method.
      `decode_params`: A dictionary to be passed as argument to the `decode`
        method.
      Each value of the dictionaries can be either a `Tensor` or any python
      constant.
    """

  @abc.abstractmethod
  def encode(self, x, encode_params, name=None):
    """Encodes a given `Tensor`.

    This method can create TensorFlow variables, which can be updated every time
    the encoding is executed. An example is an encoder that internally remembers
    the error incurred by previous encoding, and adds it to `x` in the next
    iteration, before executing the encoding.

    However, this method may be called in an entirely separate graph from all
    other methods. That is, the implementer of this class can *only* assume such
    variables can be accessed from this method but not from others.

    Args:
      x: A `Tensor`, input to be encoded.
      encode_params: A dictionary, containing the parameters needed for the
        encoding. The structure needs to be the return structure of the
        `get_params` method.
      name: `string`, name of the operation.

    Returns:
      A dictionary of `Tensor` objects representing the encoded input `x`.
    """

  @abc.abstractmethod
  def decode(self, encoded_tensors, decode_params, shape=None, name=None):
    """Decodes the encoded representation.

    This method is the inverse transformation of the `encode` method. The
    `encoded_tensors` argument is expected to be the output structure of
    `encode` method.

    Args:
      encoded_tensors: A dictionary containing `Tensor` objects,
        representing the encoded value.
      decode_params: A dictionary, containing the parameters needed for the
        decoding. The structure needs to be the return structure of the
        `get_params` method.
      shape: Required if the `decode_needs_input_shape` property is `True`. A
        shape of the original input to `encode`, if needed for decoding. Can
        be either a `Tensor`, or a python object.
      name: `string`, name of the operation.

    Returns:
      A single decoded `Tensor`.
    """


class AdaptiveEncodingStageInterface(six.with_metaclass(abc.ABCMeta)):
  """Adaptive version of the `EncodingStageInterface`.

  This class has the same functionality as the `EncodingStageInterface`, but in
  addition maintains a state, which is adaptive based on the values being
  compressed and can parameterize the way encoding functionality works. Note
  that this is useful only in case where the encoding is executed in multiple
  iterations.

  A typical implementation of this interface would be a wrapper of an
  implementation of `EncodingStageInterface, which uses the existing stateless
  transformations and adds state that controls some of the parameters returned
  by the `get_params` method.

  The important distinction is that in addition to `encoded_tensors`, the
  `encode` method of this class returns an additional dictionary of
  `state_update_tensors`. The `commutes_with_sum` property talks about summation
  of only the `encoded_tensors`. The `state_update_tensors` can be aggregated
  in more flexible ways, specified by the `state_update_aggregation_modes`
  property, before being passed to the `update_state` method.
  """

  @abc.abstractproperty
  def compressible_tensors_keys(self):
    """Keys of encoded tensors allowed to be further encoded.

    These keys correspond to tensors in object returned by the `encode` method,
    that are allowed to be further lossily compressed.

    This property does not directly impact the functionality, but is used by the
    `Encoder` class to validate composition.
    """

  @abc.abstractproperty
  def commutes_with_sum(self):
    """`True/False` based on whether the encoding commutes with sum.

    If `True`, it means that given multiple inputs `x` with the same `shape` and
    `dtype`, and the same `params` argument of the `encode` method, the
    implementation is such that every value in the returned `encoded_tensors`
    can be first summed, before being passed to the decoding functionality, and
    the output should be identical (up to numerical precision) to summing the
    fully decoded `Tensor` objects.

    Note that this also assumes that each of the `decode` methods would be used
    with the same values of `decode_params`.

    Returns:
      A boolean, `True` if the encoding commutes with sum.
    """

  @abc.abstractproperty
  def decode_needs_input_shape(self):
    """Whether original shape of the encoded object is needed for decoding.

    If `True`, it means that the `shape` of the `x` argument to the `encode`
    method needs to be provided to the `decode` method. For instance, this is
    needed for bitpacking, where inputs of multiple shapes can result in
    identical bitpacked representations.

    This property will be used by `Encoder` to efficiently realize the
    composition of implementations of this interface.
    """

  @abc.abstractproperty
  def state_update_aggregation_modes(self):
    """Aggregation mode of state update tensors.

    Returns a dictionary mapping keys appearing in `state_update_tensors`
    returned by the `encode` method to a `StateAggregationMode` object, which
    declares how should the `Tensor` objects be aggreggated.
    """

  @abc.abstractmethod
  def initial_state(self, name=None):
    """Creates an initial state.

    Args:
      name: `string`, name of the operation.

    Returns:
      A dictionary of `Tensor` objects, representing the initial state.
    """

  @abc.abstractmethod
  def update_state(self, state, state_update_tensors, name=None):
    """Updates the state.

    This method updates the `state` based on the current value of `state`, and
    (potentially aggregated) `state_update_tesors`, returned by the `encode`
    method. This will typically happen at the end of a notion of iteration.

    Args:
      state: A dictionary of `Tensor` objects, representing the current state.
        The dictionary has the same structure as return dictionary of the
        `initial_state` method.
      state_update_tensors: A dictionary of `Tensor` objects, representing the
        `state_update_tensors` returned by the `encode` method and appropriately
        aggregated.
      name: `string`, name of the operation.

    Returns:
      A dictionary of `Tensor` objects, representing the updated `state`.
    """

  @abc.abstractmethod
  def get_params(self, name=None):
    """Returns the parameters needed for encoding.

    This method returns parameters controlling the behavior of the `encode` and
    `decode` methods.

    Implementation of this method should clearly document what are the keys of
    parameters returned by this method, in order for a potential stateful
    subclass being able to adaptively modify only existing keys.

    Note that this method is not purely functional in terms of `TensorFlow`. The
    params can be derived from an internal state of the compressor. For
    instance, if a constructor optionally takes a `Variable` as an input
    argument, which is allowed to change during iterative execution, that
    `Variable`, or a function of it, would be exposed via this method.

    Args:
      name: `string`, name of the operation.

    Returns:
      A tuple `(encode_params, decode_params)`, where
      `encode_params`: A dictionary to be passed as argument to the `encode`
        method.
      `decode_params`: A dictionary to be passed as argument to the `decode`
        method.
      Each value of the dictionaries can be either a `Tensor` or any python
      constant.
    """

  @abc.abstractmethod
  def encode(self, x, encode_params, name=None):
    """Encodes a given `Tensor`.

    This method can create TensorFlow variables, which can be updated every time
    the encoding is executed. An example is an encoder that internally remembers
    the error incurred by previous encoding, and adds it to `x` in the next
    iteration, before executing the encoding.

    However, this method may be called in an entirely separate graph from all
    other methods. That is, the implementer of this class can *only* assume such
    variables can be accessed from this method but not from others.

    Args:
      x: A `Tensor`, input to be encoded.
      encode_params: A dictionary, containing the parameters needed for the
        encoding. The structure needs to be the return structure of
        `get_params` method.
      name: `string`, name of the operation.

    Returns:
      A tuple `(encoded_tensors, state_update_tensors)`, where these are:
      `encoded_tensors`: A dictionary of `Tensor` objects representing the
        encoded input `x`.
      `state_update_tensors`: A dictionary of `Tensor` objects representing
        information necessary for updating the state.
    """

  @abc.abstractmethod
  def decode(self, encoded_tensors, decode_params, shape=None, name=None):
    """Decodes the encoded representation.

    This method is the inverse transformation of the `encode` method. The
    `encoded_tensors` argument is expected to be the output structure of
    `encode` method.

    Args:
      encoded_tensors: A dictionary containing `Tensor` objects,
        representing the encoded value.
      decode_params: A dictionary, containing the parameters needed for the
        decoding. The structure needs to be the return structure of
        `get_params` method.
      shape: Required if the `decode_needs_input_shape` property is `True`. A
        shape of the original input to `encode`, if needed for decoding. Can
        be either a `Tensor`, or a python object.
      name: `string`, name of the operation.

    Returns:
      A single decoded `Tensor`.
    """