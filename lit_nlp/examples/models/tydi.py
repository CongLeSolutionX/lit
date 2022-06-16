"""LIT wrappers for T5, supporting both HuggingFace and SavedModel formats."""
import re
from typing import List

import attr
from lit_nlp.api import model as lit_model
from lit_nlp.api import types as lit_types
from lit_nlp.examples.models import model_utils
from lit_nlp.lib import utils

import tensorflow as tf
# tensorflow_text is required for T5 SavedModel
import tensorflow_text  # pylint: disable=unused-import
import transformers
from transformers import BertTokenizer
from transformers import FlaxBertForQuestionAnswering

from rouge_score import rouge_scorer

JsonDict = lit_types.JsonDict


def masked_token_mean(vectors, masks):
  """Mean over tokens.

  Args:
    vectors: <tf.float32>[batch_size, num_tokens, emb_dim]
    masks: <tf.int32>[batch_size, num_tokens]

  Returns:
    <tf.float32>[batch_size, emb_dim]
  """
  masks = tf.cast(masks, tf.float32)
  weights = masks / tf.reduce_sum(masks, axis=1, keepdims=True)
  return tf.reduce_sum(vectors * tf.expand_dims(weights, axis=-1), axis=1)


@attr.s(auto_attribs=True, kw_only=True)
class T5ModelConfig(object):
  """Config options for a T5 generation model."""
  # Input options
  inference_batch_size: int = 4
  # Generation options
  beam_size: int = 4
  max_gen_length: int = 50
  num_to_generate: int = 1
  # Decoding options
  token_top_k: int = 10
  output_attention: bool = False


def validate_t5_model(model: lit_model.Model) -> lit_model.Model:
  """Validate that a given model looks like a T5 model.

  This checks the model spec at runtime; it is intended to be used before server
  start, such as in the __init__() method of a wrapper class.

  Args:
    model: a LIT model

  Returns:
    model: the same model

  Raises:
    AssertionError: if the model's spec does not match that expected for a T5
    model.
  """
  # Check inputs
  ispec = model.input_spec()
  assert "input_text" in ispec
  assert isinstance(ispec["input_text"], lit_types.TextSegment)
  if "target_text" in ispec:
    assert isinstance(ispec["target_text"], lit_types.TextSegment)

  # Check outputs
  ospec = model.output_spec()
  assert "output_text" in ospec
  assert isinstance(
      ospec["output_text"],
      (lit_types.GeneratedText, lit_types.GeneratedTextCandidates))
  assert ospec["output_text"].parent == "target_text"

  return model

class TydiModel(lit_model.Model):
  """Wrapper class to perform a summarization task."""

  # Mapping from generic T5 fields to this task
  FIELD_RENAMES = {
      "input_text": "context",
      "target_text": "question",
  }
  @property
  def max_seq_length(self):
    return self.model.config.max_position_embeddings

  def __init__(self, 
              model_name="mrm8488/bert-multi-cased-finedtuned-xquad-tydiqa-goldp", 
              model=FlaxBertForQuestionAnswering.from_pretrained("mrm8488/bert-multi-cased-finedtuned-xquad-tydiqa-goldp"),
              tokenizer=BertTokenizer.from_pretrained("mrm8488/bert-multi-cased-finedtuned-xquad-tydiqa-goldp"),
              **config_kw):
    super().__init__()
    self.config = T5ModelConfig(**config_kw)
    self.tokenizer = tokenizer or transformers.AutoTokenizer.from_pretrained(
        model_name, use_fast=False)
    # TODO(lit-dev): switch to TFBertForPreTraining to get the next-sentence
    # prediction head as well.
    self.model = model or model_utils.load_pretrained(
        transformers.TFBertForMaskedLM,
        model_name,
        output_hidden_states=True,
        output_attentions=True)

    # # TODO(gehrmann): temp solution for ROUGE.
    self._scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    # If output is List[(str, score)] instead of just str
    self._multi_output = isinstance(self.output_spec()["output_text"],
                                    lit_types.GeneratedTextCandidates)
    self._get_pred_string = (
        lit_types.GeneratedTextCandidates.top_text if self._multi_output else
        (lambda x: x))
  ##
  # LIT API implementation
  def max_minibatch_size(self) -> int:
    # The lit.Model base class handles batching automatically in the
    # implementation of predict(), and uses this value as the batch size.
    return self.config.inference_batch_size

  def predict_minibatch(self, inputs):
    """Predict on a single minibatch of examples."""
    # tokenize the text. -> then return prediction
  
    # Text as sequence of sentencepiece ID"s.
    context =[]
    question = []
    for i in inputs:
        question.append(i['question'])
        context.append(i['context'])

    prediction_output = []
    for i in range(len(inputs)):
        model = FlaxBertForQuestionAnswering.from_pretrained("mrm8488/bert-multi-cased-finedtuned-xquad-tydiqa-goldp")
        inputs = self.tokenizer(question[i], context[i], return_tensors="jax",padding=True)
        outputs = model(**inputs)

        answer_start_index = outputs.start_logits.argmax()

        answer_end_index = outputs.end_logits.argmax()

        predict_answer_tokens = inputs.input_ids[0, answer_start_index : answer_end_index + 1]

        # creating output DIct
        output = {
            "output_text" : self.tokenizer.decode(predict_answer_tokens)
        }
        prediction_output.append(output)
    
    print('Predictions for the data is---->/n')
    print(prediction_output)

    # returning list of prediction
    return prediction_output
    

  def input_spec(self):
    return {
        "context": lit_types.TextSegment(),
        "question": lit_types.TextSegment(required=False),
    }

  def output_spec(self):
    spec = spec = {
        "output_text": lit_types.GeneratedText(parent="question")
    }
    spec["rougeL"] = lit_types.Scalar()
    return spec
