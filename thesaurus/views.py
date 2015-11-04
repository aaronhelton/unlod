from django.shortcuts import render
from django.http import HttpResponse, Http404
from django.core.urlresolvers import resolve
from django.shortcuts import get_object_or_404, render
from django.template import RequestContext, loader
from django.template.context_processors import i18n
from django.utils import translation
from django.conf import settings
from operator import itemgetter, attrgetter
from icu import Collator, Locale
from SPARQLWrapper import SPARQLWrapper, JSON, TURTLE, N3
#from v2.queries import get_preferred_label
from pure_pagination import Paginator, EmptyPage, PageNotAnInteger
import re, hashlib, json
from .models import Cache

# use this to resolve labels from UNBIS Thesaurus -> (owl:sameAs mappings) -> EuroVoc alignments 
# characterized as skos:exactMatch
EV_ENDPOINT = 'http://open-data.europa.eu/sparqlep'
NONROUTABLES = ["Alignment","Ontology","PlaceName"]

sparql = SPARQLWrapper('http://52.20.172.127:8000/catalogs/public/repositories/thesaurus')

def index(request):
  preferred_language = translation.get_language()
  querystring = ""
  target="instances"
  collator = Collator.createInstance(Locale(preferred_language))
  
  if request.GET and request.GET['aspect']:
    aspect = request.GET['aspect']
  else:
    aspect = 'categories'
    
  if aspect == 'GeographicTerm':
    querystring = "prefix unbist: <http://unontologies.s3-website-us-east-1.amazonaws.com/unbist#> select ?x where { ?x rdf:type unbist:" + aspect + " filter not exists { ?x rdf:type unbist:PlaceName } }"
  elif aspect == 'Concept' or aspect == 'Collection' or aspect == 'ConceptScheme':
    querystring = "prefix unbist: <http://unontologies.s3-website-us-east-1.amazonaws.com/unbist#> select ?x where { ?x rdf:type skos:" + aspect + " filter not exists { ?x rdf:type unbist:PlaceName } }"
  elif aspect == 'Domain' or aspect == 'MicroThesaurus':
    querystring = "prefix eu: <http://eurovoc.europa.eu/schema#> select ?x where { ?x rdf:type eu:" + aspect + " }"
  elif aspect == "by_type":
    querystring = "select distinct ?x { ?resource a ?x }"
    target="classes"
    aspect = "type"
  elif aspect == "categories":
    querystring = "select ?x where { ?x rdf:type skos:Collection }"
  elif aspect == "alphabetical":
    querystring = "prefix unbist: <http://unontologies.s3-website-us-east-1.amazonaws.com/unbist#> select ?x where { ?x rdf:type skos:Concept filter not exists { ?x rdf:type unbist:PlaceName } }"
  else:
    querystring = "select ?x where { ?x rdf:type skos:Collection }"
      
  m = hashlib.md5()
  m.update(querystring)
  md5 = m.hexdigest()
  results = []
    
  #check if results are cached already; we don't care if they have been updated recently just yet
  try:
    cache_object = Cache.objects.get(md5=md5, language=preferred_language)
    results = cache_object.result_set
    #print(results)
  except Cache.DoesNotExist, e:
    sparql.setQuery(querystring)
    sparql.setReturnFormat(JSON)
    this_results = sparql.query().convert()["results"]["bindings"]
         
    for res in this_results:
      if aspect == "by_type":
        short_name = res["x"]["value"].split("#")[1]
        if short_name in NONROUTABLES:
          next
        else:
          res["pref_label"] = short_name
          results.append(res)  
      else:
        res["pref_label"] = get_preferred_label(res["x"]["value"], preferred_language)
        results.append(res)
        
    Cache.objects.update_or_create(md5=md5,language=preferred_language,result_set=results)
    
  sorted_results = sorted(results, key=lambda tup: tup['pref_label'], cmp=collator.compare)
  try:
    page = request.GET.get('page', 1)
  except PageNotAnInteger:
    page = 1
    
  p = Paginator(sorted_results, 15, request=request)
  paginated_results = p.page(page)

  return render(request, 'thesaurus/index.html', {'results': paginated_results, 'target': target, 'aspect':aspect})

def term(request):
  preferred_language = translation.get_language()
  collator = Collator.createInstance(Locale(preferred_language))
  if request.GET and request.GET['uri']:
    uri = request.GET['uri']
    results = []
    matches = []
    local_children = ["skos:broader","skos:narrower","skos:related", "skos:member"]
    remote_children = ["skos:exactMatch", "skos:relatedMatch"]
    mirror_children = ["skos:broadMatch", "skos:narrowMatch", "skos:closeMatch"]
    
    types_q = "select ?rdf_type where { <" + uri + "> rdf:type ?rdf_type . }"
    breadcrumbs_q = "prefix eu: <http://eurovoc.europa.eu/schema#> select ?domain ?microthesaurus where { { ?domain skos:member ?microthesaurus . ?microthesaurus skos:member <" + uri + "> . } union { ?domain rdf:type eu:Domain . ?domain skos:member <" + uri + "> } . }"
    print(breadcrumbs_q)
    pref_labels_q = "select ?skos_prefLabel where { <" + uri + "> skos:prefLabel ?skos_prefLabel . } order by lang(?skos_prefLabel)"
    alt_labels_q = "select ?skos_altLabel where { <" + uri + "> skos:altLabel ?skos_altLabel filter(lang(?skos_altLabel)='" + preferred_language + "') . }"
    scope_notes_q = "select ?skos_scopeNote where { <" + uri + "> skos:scopeNote ?skos_scopeNote  filter(lang(?skos_scopeNote)='" + preferred_language + "')}"
    
    pref_label = get_preferred_label(uri, preferred_language)
    
    sparql.setQuery(types_q)
    sparql.setReturnFormat(JSON)
    t_results = sparql.query().convert()["results"]["bindings"]
    for res in t_results:
      res["label"] = res["rdf_type"]["value"].split("#")[1]
    rdf_types = t_results
    
    sparql.setQuery(breadcrumbs_q)
    sparql.setReturnFormat(JSON)
    b_results = sparql.query().convert()["results"]["bindings"]
    for res in b_results:
      res["domain"]["pref_label"] = get_preferred_label(res["domain"]["value"], preferred_language)
      if res.get('microthesaurus'):
        res["microthesaurus"]["pref_label"] = get_preferred_label(res["microthesaurus"]["value"], preferred_language)
    breadcrumbs = b_results
    
    sparql.setQuery(pref_labels_q)
    sparql.setReturnFormat(JSON)
    pref_labels = sparql.query().convert()["results"]["bindings"]
    
    sparql.setQuery(alt_labels_q)
    sparql.setReturnFormat(JSON)
    alt_labels = sparql.query().convert()["results"]["bindings"]
    
    sparql.setQuery(scope_notes_q)
    sparql.setReturnFormat(JSON)
    scope_notes = sparql.query().convert()["results"]["bindings"]

    for t in local_children:
      querystring = "select  ?" + t.replace(":","_") + " where { <" + uri + "> " + t + " ?" + t.replace(":","_") + " }"
      sparql.setQuery(querystring)
      sparql.setReturnFormat(JSON)
      this_results = sparql.query().convert()["results"]["bindings"]
      for res in this_results:
        if res[t.replace(":","_")]:
          res["pref_label"] = get_preferred_label(res[t.replace(":","_")]["value"], preferred_language)
      sorted_results = sorted(this_results, key=lambda tup: tup['pref_label'], cmp=collator.compare)
      results.append({'name':t, 'set': sorted_results})
      
    for t in remote_children:
      querystring = "select ?" + t.replace(":","_") + " where { <" + uri +"> owl:sameAs ?osa . ?" + t.replace(":","_") + " " + t + " ?osa }"
      sparql.setQuery(querystring)
      sparql.setReturnFormat(JSON)
      this_results = sparql.query().convert()["results"]["bindings"]
      if len(this_results) > 0:
        matches.append({'name':t, 'set': this_results})
      
    for t in mirror_children:
      querystring = "select ?" + t.replace(":","_") + " where { <" + uri +"> owl:sameAs ?osa . ?osa " + t + " ?" + t.replace(":","_") + " }"
      sparql.setQuery(querystring)
      sparql.setReturnFormat(JSON)
      this_results = sparql.query().convert()["results"]["bindings"]
      if len(this_results) > 0:
        matches.append({'name':t, 'set': this_results})

    return render(request, 'thesaurus/term.html', {'pref_label': pref_label, 'scope_notes': scope_notes, 'pref_labels': pref_labels, 'alt_labels': alt_labels, 'results': results, 'rdf_types': rdf_types, 'breadcrumbs': breadcrumbs, 'matches': matches})
    
    
def get_preferred_label(uri,language):
  if uri and language:
    querystring = "select ?label where { <" + uri + "> skos:prefLabel ?label filter(lang(?label) = '{{language}}')} limit 1"
    try:
      sparql.setQuery(querystring.replace("{{language}}", language))
      sparql.setReturnFormat(JSON)
      results = sparql.query().convert()["results"]["bindings"]
      if len(results) == 0:
        raise ValueError("not found")
      else:
        return results[0]["label"]["value"]
    except ValueError:
      try: 
        sparql.setQuery(querystring.replace("{{language}}", 'en'))
        sparql.setReturnFormat(JSON)
        results = sparql.query().convert()["results"]["bindings"]
        if len(results) == 0:
          raise ValueError("not found")
        else:
          return results[0]["label"]["value"]
      except ValueError:
        return uri
