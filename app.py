import re
# import pickle
# import shelve
from sqlitedict import SqliteDict

import mwparserfromhell
from mwparserfromhell.nodes.text import Text
from mwparserfromhell.nodes.wikilink import Wikilink 
import wikitextparser as wtp

import requests
import nltk
from nltk.util import ngrams
import operator
import numpy as np

import time
import operator
import sys
import csv

from utils import wtpGetLinkAnchor
from utils import get_feature_set


from flask import Flask, request, jsonify, render_template
import xgboost as xgb

'''
This API makes link recommendations for articles.
Pass the article title.
Output is a wikitext with suggestions.
'''
lang = 'simple'
wiki   = lang+'wiki'
API_URL = "https://{0}.wikipedia.org/w/api.php".format(lang)

def parse(title):
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "rvlimit": 1,
        "titles": title,
        "format": "json",
        "formatversion": "2",
    }
    headers = {"User-Agent": "My-Bot-Name/1.0"}
    req = requests.get(API_URL, headers=headers, params=params)
    res = req.json()
    revision = res["query"]["pages"][0]["revisions"][0]
    text = revision["slots"]["main"]["content"]
    return mwparserfromhell.parse(text)

# ## open datasets as shelve
# anchors = shelve.open( "./data/{0}/{0}.anchors.db".format(lang), flag='r' )
# word2vec = shelve.open("./data/{0}/{0}.w2v.filtered".format(lang), flag='r' )
# nav2vec = shelve.open("./data/{0}/{0}.nav.filtered".format(lang), flag='r' )
# ## the trained model
# import xgboost as xgb
# model = xgb.XGBClassifier()  # init model
# model.load_model('./data/{0}/0001.link.bin'.format(lang))  # load data

## open datasets as shelve
# Load the anchor dictionary (the main data structure)
# anchors = pickle.load( open("./data/{0}_pkl/{0}.anchors.pkl".format(lang),'rb'))
# ## load word2vec features
# word2vec = pickle.load( open("./data/{0}_pkl/{0}.w2v.filtered.pkl".format(lang),'rb'))
# ## load navigation-vector features
# nav2vec = pickle.load(open("./data/{0}_pkl/{0}.nav.filtered.pkl".format(lang),'rb'))


anchors = SqliteDict("./data/{0}/{0}.anchors.sqlite".format(lang))
# pageids = SqliteDict("./data/{0}_sqlite/{0}.pageids.sqlite".format(lang)) 
# redirects = SqliteDict("./data/{0}_sqlite/{0}.redirects.sqlite".format(lang)) 
word2vec = SqliteDict("./data/{0}/{0}.w2v.filtered.sqlite".format(lang))
nav2vec = SqliteDict("./data/{0}/{0}.nav.filtered.sqlite".format(lang))

## load trained model
model = xgb.XGBClassifier()  # init model
model.load_model('./data/{0}/{0}.linkmodel.bin'.format(lang))  # load data




app = Flask(__name__)
app.config["DEBUG"] = True
app.config['JSON_SORT_KEYS'] = False
CUSTOM_UA = 'reader session app -- mgerlach@wikimedia.org'

# THRESHOLD = 0.95


## load embedding
print("Try: http://127.0.0.1:5000/api/v1/addlink?title=Shri_Yantra")

@app.route('/')
def index():
    return 'Server Works!'

@app.route('/api/v1/addlink', methods=['GET'])
def get_recommendations():

    title = request.args.get('title')
    threshold = float(request.args.get('threshold' , 0.8))
    # try:
    result = process_page(title, threshold=threshold)

    result_formatted = [ {'title': title, 'wikitext_new':str(result)}]

    return jsonify(result_formatted)
    # except:
    #     return jsonify({'Error':title})



def classify_links(page, text, threshold = 0.95):
    #start_time = time.time()
    cand_prediction = {}
    # Work with the 10 most frequent candidates
    limited_cands = anchors[text]
    if len(limited_cands) > 10:
        limited_cands = dict(sorted(anchors[text].items(), key = operator.itemgetter(1), reverse = True)[:10]) 
    for cand in limited_cands:
        # get the features
        cand_feats = get_feature_set(page, text, cand, anchors, word2vec,nav2vec)
        # compute the model probability
        cand_prediction[cand] = model.predict_proba(np.array(cand_feats).reshape((1,-1)))[0,1]
    
    # Compute the top candidate
    top_candidate = max(cand_prediction.items(), key=operator.itemgetter(1))
    
    # Check if the max probability meets the threshold before returning
    if top_candidate[1] < threshold:
        return None
    #print("--- %s seconds ---" % (time.time() - start_time))
    return top_candidate

# Article parsing utility.

# For a given page return the list of all existing links and mentions
# To avoid linking what's already linked
def getLinks(wikicode, page_title):
    m = set()
    e = set()
    page_title_tmp = page_title.replace('_',' ')
    # add the page title itself
    m.add(page_title_tmp)
    e.add(page_title_tmp)
    linklist = wtp.parse(str(wikicode)).wikilinks
    for l in linklist:
        link,anchor = wtpGetLinkAnchor(l)
        m.add(anchor)
        e.add(link)
#         m.add(l.plain_text().strip())
#         e.add(l.title.strip())
    return m, e

# Split a MWPFH node <TEXT> into sentences
SENT_ENDS = [u".", u"!", u"?"]
def tokenize_sentence_split(text):
    for line in text.split("\n"):
        tok_acc = []
        for tok in nltk.word_tokenize(line):
            tok_acc.append(tok)
            if tok in SENT_ENDS:
                yield " ".join(tok_acc)
                tok_acc = []
        if tok_acc:
            yield " ".join(tok_acc)

# Actual Linking function
def process_page(page, threshold = 0.95):
    page_wikicode = parse(page)
    page_wikicode_init= str(page_wikicode) # save the initial state
    linked_mentions, linked_links = getLinks(page_wikicode, page)
    tested_mentions = set()
    for gram_length in range(10, 0, -1):
        #print("Scanning ", gram_length, "Grams")
        # Parsing the tree can be done once
        for node in page_wikicode.filter(recursive= False):
            if isinstance(node, Text):
                lines = node.split("\n")
                for line in lines:

                    for sent in tokenize_sentence_split(line):
                        grams = list(ngrams(sent.split(), gram_length))
    
                        for gram in grams:
                            mention = ' '.join(gram).lower()
                            # if the mention exist in the DB 
                            # it was not previously linked (or part of a link)
                            # none of its candidate links is already used
                            # it was not tested before (for efficiency)
 
                            if (mention in anchors and
                                not any(mention in s for s in linked_mentions) and
                                not bool(set(anchors[mention].keys()) & linked_links) and
                                mention not in tested_mentions):
                                #logic
                                #print("testing:", mention, len(anchors[mention]))
                                candidate = classify_links(page, mention, threshold=threshold)
                                if candidate:
                                    candidate_link, candidate_proba = candidate
                                    #print(">> ", mention, candidate)
                                    ############## Critical ##############
                                    # Insert The Link in the current wikitext
                                    match = re.compile(r'(?<!\[\[)(?<!-->)\b{}\b(?![\w\s]*[\]\]])'.format(re.escape(mention)))
                                    newval, found = match.subn("[[" + candidate_link  +  "|" + mention+  "|pr=" + str(candidate_proba) + "]]", node.value, 1)
                                    node.value = newval
                                    ######################################
                                    # Book-keeping
                                    linked_mentions.add(mention)
                                    linked_links.add(candidate)
                                # More Book-keeping
                                tested_mentions.add(mention)

    return page_wikicode

if __name__ == '__main__':
    '''
    '''
    app.run(host='0.0.0.0')