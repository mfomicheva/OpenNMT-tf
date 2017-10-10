"""Sequence tagger."""

from __future__ import print_function

import tensorflow as tf

from opennmt.models.model import Model
from opennmt.utils.misc import count_lines
from opennmt.utils.losses import masked_sequence_loss


class SequenceTagger(Model):
  """A sequence tagger."""

  def __init__(self,
               inputter,
               encoder,
               labels_vocabulary_file_key,
               crf_decoding=False,
               name="seqtagger"):
    """Initializes a sequence tagger.

    Args:
      inputter: A `onmt.inputters.Inputter` to process the input data.
      encoder: A `onmt.encoders.Encoder` to encode the input.
      labels_vocabulary_file_key: The data configuration key of the labels
        vocabulary file containing one label per line.
      crf_decoding: If `True`, add a CRF layer after the encoder.
      name: The name of this model.
    """
    super(SequenceTagger, self).__init__(name)

    self.encoder = encoder
    self.inputter = inputter
    self.labels_vocabulary_file_key = labels_vocabulary_file_key
    self.crf_decoding = crf_decoding

  def _initialize(self, metadata):
    self.inputter.initialize(metadata)
    self.labels_vocabulary_file = metadata[self.labels_vocabulary_file_key]
    self.num_labels = count_lines(self.labels_vocabulary_file)

  def _get_serving_input_receiver(self):
    return self.inputter.get_serving_input_receiver()

  def _get_features_length(self, features):
    return self.inputter.get_length(features)

  def _get_labels_length(self, labels):
    return None

  def _get_features_builder(self, features_file):
    dataset = self.inputter.make_dataset(features_file)
    process_fn = self.inputter.process
    padded_shapes_fn = lambda: self.inputter.padded_shapes
    return dataset, process_fn, padded_shapes_fn

  def _get_labels_builder(self, labels_file):
    labels_vocabulary = tf.contrib.lookup.index_table_from_file(
        self.labels_vocabulary_file,
        vocab_size=self.num_labels)

    dataset = tf.contrib.data.TextLineDataset(labels_file)
    process_fn = lambda x: labels_vocabulary.lookup(tf.string_split([x]).values)
    padded_shapes_fn = lambda: [None]
    return dataset, process_fn, padded_shapes_fn

  def _build(self, features, labels, params, mode, config):
    length = self._get_features_length(features)

    with tf.variable_scope("encoder"):
      inputs = self.inputter.transform_data(
          features,
          mode,
          log_dir=config.model_dir)

      encoder_outputs, _, encoder_sequence_length = self.encoder.encode(
          inputs,
          sequence_length=length,
          mode=mode)

    with tf.variable_scope("generator"):
      logits = tf.layers.dense(
          encoder_outputs,
          self.num_labels)

    if mode != tf.estimator.ModeKeys.TRAIN:
      if self.crf_decoding:
        transition_params = tf.get_variable(
            "transitions", shape=[self.num_labels, self.num_labels])
        tags_id, _ = tf.contrib.crf.crf_decode(
            logits,
            transition_params,
            encoder_sequence_length)
        tags_id = tf.cast(tags_id, tf.int64)
      else:
        tags_prob = tf.nn.softmax(logits)
        tags_id = tf.argmax(tags_prob, axis=2)

      labels_vocab_rev = tf.contrib.lookup.index_to_string_table_from_file(
          self.labels_vocabulary_file,
          vocab_size=self.num_labels)

      predictions = {
          "length": encoder_sequence_length,
          "labels": labels_vocab_rev.lookup(tags_id)
      }
    else:
      predictions = None

    return logits, predictions

  def _compute_loss(self, features, labels, outputs):
    length = self._get_features_length(features)
    if self.crf_decoding:
      log_likelihood, _ = tf.contrib.crf.crf_log_likelihood(
          outputs,
          tf.cast(labels, tf.int32),
          length)
      return tf.reduce_mean(-log_likelihood)
    else:
      return masked_sequence_loss(logits, labels, length)

  def print_prediction(self, prediction, params=None, stream=None):
    labels = prediction["labels"][:prediction["length"]]
    sent = b" ".join(labels)
    print(sent.decode("utf-8"), file=stream)
