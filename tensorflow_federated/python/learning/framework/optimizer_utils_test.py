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
"""Tests for learning.framework.optimizer_utils."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import parameterized
import numpy as np
import tensorflow as tf

from tensorflow_federated.python.common_libs import test
from tensorflow_federated.python.learning import model_examples
from tensorflow_federated.python.learning.framework import optimizer_utils
from tensorflow_federated.python.tensorflow_libs import tensor_utils


class DummyClientDeltaFn(optimizer_utils.ClientDeltaFn):

  @property
  def variables(self):
    return []

  def __call__(self, dataset, initial_weights):
    weights_delta = tf.contrib.framework.nest.map_structure(
        lambda x: -1.0 * x, initial_weights)
    client_weight = tf.constant(3.0)
    return optimizer_utils.ClientOutput(
        weights_delta,
        weights_delta_weight=client_weight,
        model_output={},
        optimizer_output={'client_weight': client_weight})


class ServerTest(test.TestCase, parameterized.TestCase):

  # pyformat: disable
  @parameterized.named_parameters(
      ('_sgd', lambda: tf.train.GradientDescentOptimizer(learning_rate=0.1),
       0.1, 0),
      # It looks like Adam introduces 2 + 2*num_model_variables additional vars.
      ('_adam', lambda: tf.train.AdamOptimizer(  # pylint: disable=g-long-lambda
          learning_rate=0.1, beta1=0.0, beta2=0.0, epsilon=1.0), 0.05, 6))
  # pyformat: enable
  def test_server_eager_mode(self, optimizer_fn, updated_val,
                             num_optimizer_vars):
    model_fn = lambda: model_examples.TrainableLinearRegression(feature_dim=2)

    server_state = optimizer_utils.server_init(model_fn, optimizer_fn)
    train_vars = server_state.model.trainable
    self.assertAllClose(train_vars['a'].numpy(), np.array([[0.0], [0.0]]))
    self.assertEqual(train_vars['b'].numpy(), 0.0)
    self.assertEqual(server_state.model.non_trainable['c'].numpy(), 0.0)
    self.assertLen(server_state.optimizer_state, num_optimizer_vars)
    weights_delta = tensor_utils.to_odict({
        'a': tf.constant([[1.0], [0.0]]),
        'b': tf.constant(1.0)
    })
    server_state = optimizer_utils.server_update_model(
        server_state, weights_delta, model_fn, optimizer_fn)

    train_vars = server_state.model.trainable
    # For SGD: learning_Rate=0.1, update=[1.0, 0.0], initial model=[0.0, 0.0],
    # so updated_val=0.1
    self.assertAllClose(train_vars['a'].numpy(), [[updated_val], [0.0]])
    self.assertAllClose(train_vars['b'].numpy(), updated_val)
    self.assertEqual(server_state.model.non_trainable['c'].numpy(), 0.0)

  @test.graph_mode_test
  def test_server_graph_mode(self):
    optimizer_fn = lambda: tf.train.GradientDescentOptimizer(learning_rate=0.1)
    model_fn = lambda: model_examples.TrainableLinearRegression(feature_dim=2)

    # Explicitly entering a graph as a default enables graph-mode.
    with tf.Graph().as_default() as g:
      server_state_op = optimizer_utils.server_init(model_fn, optimizer_fn)
      init_op = tf.group(tf.global_variables_initializer(),
                         tf.local_variables_initializer())
      g.finalize()
      with self.session() as sess:
        sess.run(init_op)
        server_state = sess.run(server_state_op)
    train_vars = server_state.model.trainable
    self.assertAllClose(train_vars['a'], [[0.0], [0.0]])
    self.assertEqual(train_vars['b'], 0.0)
    self.assertEqual(server_state.model.non_trainable['c'], 0.0)
    self.assertEmpty(server_state.optimizer_state)

    with tf.Graph().as_default() as g:
      # N.B. Must use a fresh graph so variable names are the same.
      weights_delta = tensor_utils.to_odict({
          'a': tf.constant([[1.0], [0.0]]),
          'b': tf.constant(2.0)
      })
      update_op = optimizer_utils.server_update_model(
          server_state, weights_delta, model_fn, optimizer_fn)
      init_op = tf.group(tf.global_variables_initializer(),
                         tf.local_variables_initializer())
      g.finalize()
      with self.session() as sess:
        sess.run(init_op)
        server_state = sess.run(update_op)
    train_vars = server_state.model.trainable
    # learning_Rate=0.1, update is [1.0, 0.0], initial model is [0.0, 0.0].
    self.assertAllClose(train_vars['a'], [[0.1], [0.0]])
    self.assertAllClose(train_vars['b'], 0.2)
    self.assertEqual(server_state.model.non_trainable['c'], 0.0)

  def test_orchestration(self):
    iterative_process = optimizer_utils.build_model_delta_optimizer_tff(
        model_fn=model_examples.TrainableLinearRegression,
        model_to_client_delta_fn=lambda _: DummyClientDeltaFn())
    self.assertEqual(
        str(iterative_process.initialize.type_signature),
        '( -> <model=<trainable=<a=float32[2,1],b=float32>,'
        'non_trainable=<c=float32>>,'
        'optimizer_state=<float32[2,1],float32>>)')
    self.assertEqual(
        str(iterative_process.next.type_signature),
        '(<<model=<trainable=<a=float32[2,1],b=float32>,'
        'non_trainable=<c=float32>>,'
        'optimizer_state=<float32[2,1],float32>>,'
        '<trainable=<a=float32[2,1],b=float32>,'
        'non_trainable=<c=float32>>> -> '
        '<model=<trainable=<a=float32[2,1],b=float32>,'
        'non_trainable=<c=float32>>,'
        'optimizer_state=<float32[2,1],float32>>)')


if __name__ == '__main__':
  # We default to TF 2.0 behavior, including eager execution, and use the
  # @graph_mode_test annotation for graph-mode (sess.run) tests.
  tf.compat.v1.enable_v2_behavior()
  test.main()