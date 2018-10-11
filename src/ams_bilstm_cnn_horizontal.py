'''Trains a Bidirectional LSTM on the arXiv AMS environment classification task.

adapted from the official Keras examples:
https://github.com/keras-team/keras/blob/master/examples/imdb_bidirectional_lstm.py
'''

# of all the weird dependency hells...
# needed exactly cuda 9.0 and libcudnn 7, latter obtainable via:
# http://developer.download.nvidia.com/compute/machine-learning/repos/ubuntu1404/x86_64/
# the former via (and you need the 3 updates for 9.0)
# https://developer.nvidia.com/cuda-90-download-archive?target_os=Linux&target_arch=x86_64&target_distro=Ubuntu&target_version=1704&target_type=deblocal

from __future__ import print_function
import numpy as np
import gc
import json

import tensorflow as tf
from keras.models import Sequential, Model
from keras.layers import Dense, Dropout, LSTM, Bidirectional, TimeDistributed, Conv1D, GlobalMaxPooling1D, Flatten, BatchNormalization
from keras.layers.merge import concatenate
from keras import metrics
from keras import backend as K
from keras.callbacks import ModelCheckpoint, EarlyStopping
from sklearn.metrics import classification_report
from sklearn.utils.class_weight import compute_class_weight

import arxiv

# Use full CPU capacity, where possible
gpu_options = tf.GPUOptions(
    per_process_gpu_memory_fraction=0.48, allow_growth=True)
config = tf.ConfigProto(intra_op_parallelism_threads=16,
                        inter_op_parallelism_threads=16, allow_soft_placement=True, gpu_options=gpu_options)

session = tf.Session(config=config)
K.set_session(session)

# 08.2017 (mathformula replacements)
# Analyzing the arxiv dataset seems to indicate a maxlen of 300 is needed to fit 99.2% of the data
#                                               a maxlen of 150 fits 94.03%, and a maxlen of 600 covers 99.91% of paragraphs
# 08.2018 (subformula lexemes)
# Analyzing the arxiv dataset seems to indicate a maxlen of 960 is needed to fit 99.2% of the data
#                                               a maxlen of 480 fits 96.03%, and a maxlen of 300 covers 90.0% of paragraphs

setup_labels = 'f1-envs'  # False
classes_for_label = {
    "no-other": 22,
    "strict-envs": 11,
    "stricter-envs": 10,
    "f1-envs": 7,
    "definition-binary": 2
}
n_classes = 23  # ams classes/labels (0-22)
if setup_labels and setup_labels in classes_for_label:
    n_classes = classes_for_label[setup_labels]

maxlen = 480
layer_size = 128  # maxlen // 4
batch = 256
model_file = "bilstm%d_conv1d_batch%d_cat%d_exps" % (
    layer_size, batch, n_classes)

print('Loading data...')
x_train, x_test, y_train, y_test = arxiv.load_data(maxlen=None, start_char=None, num_words=1_000_000,
                                                   shuffle=False, setup_labels=setup_labels, full_data=False, max_per_class=5_000)
print(len(x_train), 'train sequences')
print(len(x_test), 'test sequences')
gc.collect()

y_train = np.array(y_train)
y_test = np.array(y_test)

print('x_train shape:', x_train.shape)
print('x_test shape:', x_test.shape)
print('y_train shape:', y_train.shape)
print('y_test shape:', y_test.shape)

class_weights = compute_class_weight('balanced', np.unique(y_train), y_train)

(embedding_layer, input_1) = arxiv.build_embedding_layer(
    with_input=True, maxlen=maxlen, mask_zero=False)
gc.collect()

print("setting up model layout...")
use_dropout = True

if use_dropout:
    embedding_layer = Dropout(0.2)(embedding_layer)

bilstm = Bidirectional(
    LSTM(layer_size))(embedding_layer)
if use_dropout:
    bilstm = Dropout(0.2)(bilstm)

tower_32 = Conv1D(filters=16, kernel_size=32, strides=4,
                  activation="relu", padding='same')(embedding_layer)
tower_32 = GlobalMaxPooling1D()(BatchNormalization()(tower_32))
if use_dropout:
    tower_32 = Dropout(0.2)(tower_32)

tower_16 = Conv1D(filters=32, kernel_size=16, strides=3,
                  activation="relu", padding='same')(embedding_layer)
tower_16 = GlobalMaxPooling1D()(BatchNormalization()(tower_16))
if use_dropout:
    tower_16 = Dropout(0.2)(tower_16)

tower_8 = Conv1D(filters=64, kernel_size=8, strides=2,
                 activation="relu", padding='same')(embedding_layer)
tower_8 = GlobalMaxPooling1D()(BatchNormalization()(tower_8))
if use_dropout:
    tower_8 = Dropout(0.2)(tower_8)

tower_4 = Conv1D(filters=128, kernel_size=4,
                 activation="relu", padding='same')(embedding_layer)
tower_4 = GlobalMaxPooling1D()(BatchNormalization()(tower_4))
if use_dropout:
    tower_4 = Dropout(0.2)(tower_4)

output = concatenate([bilstm, tower_32, tower_16, tower_8, tower_4])

output = Dense(layer_size, activation='relu')(output)

output = Dense(n_classes, activation='softmax')(output)
# summarize the model
model = Model(inputs=[input_1], outputs=output)
model.compile(loss='sparse_categorical_crossentropy',
              optimizer="adam",
              weighted_metrics=[metrics.sparse_categorical_accuracy])

# Print model summary
print(model.summary())

# Checkpoints: 1) save best model at epoch end, 2) stop early when metric stops improving
checkpoint = ModelCheckpoint(model_file+"-checkpoint.h5",
                             monitor='val_weighted_sparse_categorical_accuracy',
                             verbose=1,
                             save_best_only=True,
                             mode='max')

earlystop = EarlyStopping(monitor='val_weighted_sparse_categorical_accuracy',
                          min_delta=0.001,
                          patience=2,
                          verbose=0, mode='auto')

# Perform training
print('Training model...')
model.fit(x_train, y_train,
          # what is the optimum here? the average arXiv document seems to have 110 paragraphs ?!
          batch_size=batch,  # 32, 64, 128
          # Classifies into: acknowledgement(0), algorithm(1), caption(2), proof(3), assumption(4), definition(5), problem(6), remark(7), other(8)
          # f1-envs only, based on ratios in full dataset
          # https://docs.google.com/spreadsheets/d/16I9969_QcU4J9EtglGKZpLHVeNcFIeDGNU4trhi53Vc/edit#gid=1538283102
          #   class_weight={0: 2500, 1: 1000, 2: 12500, 3: 2.6,
          #                 4: 450, 5: 17, 6: 400, 7: 17, 8: 0.5},
          #
          class_weight=class_weights,
          epochs=50,
          verbose=1,
          callbacks=[checkpoint, earlystop],
          validation_split=0.2)

# serialize model to JSON
print("Saving model to disk : %s " % model_file)
model.save(model_file+'.h5')

print("Per-class test measures:")
# y_pred = model.predict_classes(x_test, verbose=1, batch_size=batch)
y_prob = model.predict(x_test, batch_size=batch, verbose=1)
y_pred = y_prob.argmax(axis=-1)

print(classification_report(y_test, y_pred))