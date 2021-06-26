import json
import numpy as np
import tensorflow as tf
import tensorflow.keras as keras
from tensorflow.keras.models import model_from_json
from tensorflow.keras.preprocessing.sequence import pad_sequences
from tensorflow.python.client import device_lib
from sklearn.model_selection import train_test_split
from wandb.keras import WandbCallback

from utils import return_json_file_content


def session_indexed(s):
    """
    Converts a session (of actions) to indices and adds start/end tokens

    :param s: list of actions in a session (i.e 'add','detail', etc)
    :return:
    """
    # assign an integer to each possible action token
    action_to_idx = {'start': 0, 'end': 1, 'add': 2, 'remove': 3, 'purchase': 4, 'detail': 5, 'view': 6}
    return [action_to_idx['start']] + [action_to_idx[e] for e in s] + [action_to_idx['end']]


def train_lstm_model(x, y,
                     epochs=200,
                     patience=10,
                     lstm_dim=48,
                     batch_size=128,
                     lr=1e-3):
    """
    Train an LSTM to predict purchase (1) or abandon (0)

    :param x: session sequences
    :param y: target labels
    :param epochs: num training epochs
    :param patience: early stopping patience
    :param lstm_dim: lstm units
    :param batch_size: batch size
    :param lr: learning rate
    :return:
    """

    # Verfiy if GPU/CPU is being used
    print("Print out system device...")
    print(device_lib.list_local_devices())
    print("Starting training now...")


    X_train, X_test, y_train, y_test = train_test_split(x,y)
    # pad sequences for training in batches
    max_len = max(len(_) for _ in x)
    X_train = pad_sequences(X_train, padding="post",value=7, maxlen=max_len)
    X_test = pad_sequences(X_test, padding="post", value=7, maxlen=max_len)

    # convert to one-hot
    X_train = tf.one_hot(X_train, depth=7)
    X_test = tf.one_hot(X_test, depth=7)

    y_train = np.array(y_train)
    y_test = np.array(y_test)

    # Define Model
    model = keras.Sequential()
    model.add(keras.layers.InputLayer(input_shape=(None,7)))
    # Masking layer ignores padded time-steps
    model.add(keras.layers.Masking())
    model.add(keras.layers.LSTM(lstm_dim))
    model.add(keras.layers.Dense(1,activation='sigmoid'))
    model.summary()

    # Some Hyper Params
    opt = keras.optimizers.Adam(learning_rate=lr)
    loss = keras.losses.BinaryCrossentropy()
    es = keras.callbacks.EarlyStopping(monitor='val_loss',
                                       patience=patience,
                                       verbose=1,
                                       restore_best_weights=True)

    # Include wandb callback for tracking
    callbacks = [es, WandbCallback()]
    model.compile(optimizer=opt,
                  loss=loss,
                  metrics=['accuracy'])

    # Train Model
    model.fit(X_train,y_train,
              validation_data=(X_test,y_test),
              batch_size=batch_size,
              epochs=epochs,
              callbacks=callbacks)

    # return trained model
    # NB: to store model as Metaflow Artifact it needs to be pickle-able!
    return model.to_json(), model.get_weights()


def make_predictions(model, model_weights, test_file: str):

    # re-init model and load weights
    model = model_from_json(model)
    model.set_weights(model_weights)

    # load test data
    test_queries = return_json_file_content(test_file)
    X_test = []

    # extract actions from test input
    for t in test_queries:
        session = t['query']
        actions = []
        for e in session:
            # NB : we are disregarding search actions here
            if e['product_action'] == None and e['event_type'] == 'pageview':
                actions.append('view')
            elif e['product_action'] != None:
                actions.append(e['product_action'])
        X_test.append(actions)

    # Convert to index, pad & one-hot
    max_len = max([len(_) for _ in X_test])
    X_test = [session_indexed(_) for _ in X_test]
    X_test = pad_sequences(X_test, padding="post", value=7, maxlen=max_len)
    X_test = tf.one_hot(X_test, depth=7)

    # make predictions
    preds = model.predict(X_test,batch_size=128)
    preds = (preds > 0.5).reshape(-1).astype(int).tolist()

    # Convert to required prediction format
    preds = [{'label': pred} for pred in preds]

    assert len(preds) == len(test_queries)

    return preds