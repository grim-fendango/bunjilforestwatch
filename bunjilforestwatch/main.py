
from __future__ import with_statement

LANSAT_CELL_AREA = (185*170) # sq.km  http://iic.gis.umn.edu/finfo/land/landsat2.htm

import logging

logging.basicConfig(level=logging.DEBUG)

from django.utils import html # used for entry.html markup


#import sqlbuilder
import eeservice
#from eeservice import EE_CREDENTIALS
import oauth2client.client  # pylint: disable=g-bad-import-order

import ee
import mailer
import base64
import datetime
import re
import os
import geojson

from google.appengine.api import files
from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.ext import blobstore
from google.appengine.ext import db
from google.appengine.ext.webapp import blobstore_handlers
from webapp2_extras import sessions

from google.appengine.api import memcache
from apiclient.discovery import build
from oauth2client.appengine import OAuth2Decorator

import settings


'''
decorator = OAuth2Decorator(
    client_id=settings.MY_LOCAL_SERVICE_ACCOUNT,
    client_secret=settings.MY_LOCAL_PRIVATE_KEY_FILE,
    scope="https://www.googleapis.com/auth/fusiontables",
    user_agent='bunjilfw')
'''
import httplib2
from google.appengine.api import memcache
from oauth2client.appengine import AppAssertionCredentials

#import ftclient

import json
import webapp2

import cache
import counters
import facebook
import filters
import models
import twitter
import utils
from urlparse import urlparse


#from google.appengine.ext.webapp.util import login_required

PRODUCTION_MODE = not os.environ.get(
    'SERVER_SOFTWARE', 'Development').startswith('Development')
    
if not PRODUCTION_MODE:
    from google.appengine.tools.devappserver2.python import sandbox
    sandbox._WHITE_LIST_C_MODULES += ['_ctypes', 'gestalt']
    disable_ssl_certificate_validation = True # bug in HTTPlib i think

    
def rendert(s, p, d={}):
    session = s.session
    d['session'] = session

    if 'user' in session:
        d['user'] = session['user']
    # this is still set after logout (i'm not sure why it's set at all), so use this workaround
    elif 'user' in d:
        del d['user']

    for k in ['login_source']:
        if k in session:
            d[k] = session[k]

    d['messages'] = s.get_messages()
    d['active'] = p.partition('.')[0]

    if settings.GOOGLE_ANALYTICS:
        d['google_analytics'] = settings.GOOGLE_ANALYTICS

    s.response.out.write(utils.render(p, d))

class BaseHandler(webapp2.RequestHandler):
    def render(self, _template, context={}):
        context['session'] = self.session
        context['user'] = self.session.get('user')
        context['messages'] = self.get_messages()
        context['active'] = _template.partition('.')[0]

        if 'bunjilfw' in self.request.url:
            self.add_message("warning", "Test Instance - Production is now at bunjilforestwatch.net")
        if 'localhost' in self.request.url:
            self.add_message("warning", "Local - Production instance at <a href='http://www.bunjilforestwatch.net'>www.bunjilforestwatch.net</a>")
       
        for k in ['login_source']:
            if k in self.session:
                context[k] = self.session[k]

        if settings.GOOGLE_ANALYTICS:
            context['google_analytics'] = settings.GOOGLE_ANALYTICS
       #context['show_navbar'] = True

        #logging.info('BaseHandler: render template %s with context <<%s>>,', _template, context)
        #logging.debug('BaseHandler: messages %s', context['messages'])
        #print '\033[1;33mRed like Radish\033[1;m'
        #print '\033[1;34mRed like Radish\033[1;m \x1b[0m'
        #print('\033[31m' + 'some red text')
        #print('\033[30m' + 'reset to default color')

        logging.debug('BaseHandler:\033[1;31m Color Console Test\033[1;m  \x1b[0m %s', "Reset to Default Color")

        rv = utils.render(_template, context)

        self.response.write(rv)

    def dispatch(self):
        self.session_store = sessions.get_store(request=self.request)
        #logging.info('BaseHandler:dispatch %s', self.request)

        try:
            webapp2.RequestHandler.dispatch(self)
        finally:
            self.session_store.save_sessions(self.response)

    @webapp2.cached_property
    def session(self):
        return self.session_store.get_session(backend='datastore')

    # This should be called anytime the session data needs to be updated.
    # session['var'] = var should never be used, except in this function
    #This function adds the below data to the data returned to the template. 
    def populate_user_session(self, user=None):
        if 'user' not in self.session and not user:
            logging.error("populate_user_session() - no user!")
            return
        elif not user:
            user = cache.get_user(self.session['user']['name'])
        
        self.session['user'] = {
            'admin': users.is_current_user_admin(),
            'avatar': user.gravatar(),
            'email': user.email,
            'key': str(user.key()),
            'name': user.name,
            'token': user.token,
            'role' : user.role
        }
             
        self.session['journals'] = cache.get_journal_list(db.Key(self.session['user']['key']))
        self.session['areas_list']    = cache.get_areas_list(db.Key(self.session['user']['key'])) #TODO This list can be long and expensive.
        self.session['following_areas_list'] = cache.get_following_areas_list(self.session['user']['key']) # used for basehandler menu.

    MESSAGE_KEY = '_flash_message'
    def add_message(self, level, message):
        self.session.add_flash(message, level, BaseHandler.MESSAGE_KEY)

    def get_messages(self):
        return self.session.get_flashes(BaseHandler.MESSAGE_KEY)

    def process_credentials(self, name, email, source, uid):
        user = models.User.all().filter('%s_id' %source, uid).get()

        if not user:
            registered = False
            self.session['register'] = {'name': name, 'email': email, 'source': source, 'uid': uid}
        else:
            registered = True
            self.populate_user_session(user)
            self.session['login_source'] = source
            user.put() # to update last_active

        return user, registered

    def logout(self):
        for k in ['user', 'journals', 'areas']:
            if k in self.session:
                del self.session[k]

class BaseUploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    session_store = None

    def add_message(self, level, message):
        self.session.add_flash(message, level, BaseHandler.MESSAGE_KEY)
        self.store()

    def store(self):
        self.session_store.save_sessions(self.response)

    @webapp2.cached_property
    def session(self):
        if not self.session_store:
            self.session_store = sessions.get_store(request=self.request)
        return self.session_store.get_session(backend='datastore')


class EarthEngineWarmUpHandler(BaseHandler):
    initEarthEngine = eeservice.EarthEngineService()

    def get(self):
        logging.debug('main.py EarthEngineWarmUpHandler')
        initEarthEngine.isReady()
        self.response.status = 202 #The request has been accepted for processing
        self.response.write("")
        return

class MainPage(BaseHandler):

    def get(self):
        if 'user' in self.session:
            #THIS CAN BE OPTIMISED
            following = cache.get_by_keys(cache.get_following(self.session['user']['name']), 'User') # for journal not areas
            followers = cache.get_by_keys(cache.get_followers(self.session['user']['name']), 'User') # for journal not areas
            journals = cache.get_journals(db.Key(self.session['user']['key']))
            areas = cache.get_areas(db.Key(self.session['user']['key'])) # areas created by this user.
            following_areas = cache.get_following_areas(self.session['user']['name'])
            other_areas = cache.get_other_areas(self.session['user']['name'], db.Key(self.session['user']['key']))
            #print  "MainHandler areas: ", areas,  " following_areas: ",  following_areas, " other_areas: ", other_areas
            self.populate_user_session() #Only need to do this when areas, journals  or followers change
            self.render('index-user.html', {
                'activities': cache.get_activities_follower(self.session['user']['name']),
                'username' : self.session['user']['name'],
                'user': self.session['user']['name'],
                'journals': journals,
                'thisuser': True,
                'token': self.session['user']['token'],
                'following': following, #other users
                'followers': followers,#other users
                'areas': areas,
                'following_areas': following_areas,
                'other_areas': other_areas, #other areas.
                'show_navbar': True
            })
        else:
            self.render('index.html', {
                        'show_navbar': False           
                                      }) # not logged in.

class ViewAreas(BaseHandler):

     def get(self, username):
        print ViewAreas
        if 'user' in self.session:
#        
            areas = cache.get_areas(db.Key(self.session['user']['key']))
            all_areas = cache.get_all_areas()
            #logging.info( "areas = %s", areas)
    
            self.render('view-areas.html', {             
                'thisuser': True,
                'token': self.session['user']['token'],
                'areas': areas,
                'show_navbar': True
            })
        else:
            self.render('index.html', {
                        'show_navbar': False           
                                      }) # not logged in.

class FacebookCallback(BaseHandler):
    def get(self):
        if 'code' in self.request.GET and 'local_redirect' in self.request.GET:
            local_redirect = self.request.get('local_redirect')
            access_dict = facebook.access_dict(self.request.get('code'), {'local_redirect': local_redirect})

            if access_dict:
                self.session['access_token'] = access_dict['access_token']
                self.redirect(webapp2.uri_for(local_redirect, callback='callback'))
                return

        self.redirect(webapp2.uri_for('main'))

            
class GoogleLogin(BaseHandler):
    def get(self): 
        current_user = users.get_current_user()
        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())
        if not registered:
            if 'role' in self.request.GET:
                role = self.request.get('role')
            else:
                role ="unknown"
            self.redirect(webapp2.uri_for('register', role=role))
                          
        else:
            self.redirect(webapp2.uri_for('main'))

              
    def get2(self, protected_url): 
        current_user = users.get_current_user()
        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())
        if not registered:
            self.redirect(webapp2.uri_for('register'))
        else:
            #if protected_url == None:
            #self.redirect(webapp2.uri_for('main'))
            #else:
            self.redirect(protected_url)
                        
class FacebookLogin(BaseHandler):
    def get(self):
        if 'callback' in self.request.GET:
            user_data = facebook.graph_request(self.session['access_token'])

            if user_data is not False and 'username' in user_data and 'email' in user_data:
                user, registered = self.process_credentials(user_data['username'], user_data['email'], models.USER_SOURCE_FACEBOOK, user_data['id'])

                if not registered:
                    self.redirect(webapp2.uri_for('register'))
                    return
        else:
            self.redirect(facebook.oauth_url({'local_redirect': 'login-facebook'}, {'scope': 'email'}))
            return

        self.redirect(webapp2.uri_for('main'))

            
class Register(BaseHandler):
    USERNAME_RE = re.compile("^[a-z0-9][a-z0-9_]+$")

    def get(self):
        return self.post()

    def post(self):
        
        if 'register' in self.session:
            errors = {}
            if 'role' in self.request.POST:
                role = self.request.get('role')
                #print 'post'
            elif 'role' in self.request.GET:
                role = self.request.get('role')
                #print 'get '  + role
            if not role or role=='unknown':
                role = self.request.get('roleoptionsRadios')
                #print 'options ' + role
            if not role:
                role = "unknown"
            if 'submit' in self.request.POST:
                username = self.request.get('username')
                lusername = username.lower()
                email = self.request.get('email')
                lusers = models.User.all(keys_only=True).filter('lname', lusername).get()
                if not Register.USERNAME_RE.match(lusername):
                    if username and username[0] == '_':
                        errors['username'] = 'Username cannot begin with a dash.'
                    else:    
                        errors['username'] = 'Username may only contain alphanumeric characters or dashes.'
                elif lusername in RESERVED_NAMES or lusers:
                    errors['username'] = 'Username is already taken.'
                else:
                    source = self.session['register']['source']
                    uid = self.session['register']['uid']

                    if not email:
                        errors['email'] = 'You must have an email to use this service.'
                        email = None

                    user = models.User.get_or_insert(username,
                        role=role,
                        name=username,
                        lname=lusername,
                        email=email,
                        facebook_id=uid if source == models.USER_SOURCE_FACEBOOK else None,
                        google_id=uid if source == models.USER_SOURCE_GOOGLE else None,
                        token=base64.urlsafe_b64encode(os.urandom(30))[:32],
                    )
                    
                    if getattr(user, '%s_id' %source) != uid:
                        errors['username'] = 'Username is already taken.'
                    else:
                        del self.session['register']
                        self.populate_user_session(user)
                        counters.increment(counters.COUNTER_USERS)
                        if role == 'local':
                            self.add_message('Success', '%s, Welcome to Bunjil Forest Watch. Now create a new area that you want monitored.' %user)
                            self.redirect(webapp2.uri_for('new-area'))
                        else:
                            self.add_message('Success', '%s, Welcome new volunteer. Choose an area to follow.' %user)
                            self.redirect(webapp2.uri_for('main'))
                        return
            else:
                email = self.session['register']['email']
                username = email.split('@')[0] # use first part of email as suggested username

            self.render('register.html', {'username': username, 'email': email, 'errors': errors, 'role': role})
        else:
            self.redirect(webapp2.uri_for('main'))

class NewUserHandler(BaseHandler):
  
    def get(self):
  
        if 'role' in self.request.GET:
            role = self.request.get('role')
        else:
            role = "unknown"
        self.render('new-user.html', {
                        'show_navbar': False,
                        'role': role           
        }) 

    def post(self):
        if 'register' in self.session:
            errors = {}

            if 'submit' in self.request.POST:
                username = self.request.get('username')
                lusername = username.lower()
                email = self.request.get('email')
                lusers = models.User.all(keys_only=True).filter('lname', lusername).get()

                if not Register.USERNAME_RE.match(lusername):
                    errors['username'] = 'Username may only contain alphanumeric characters or dashes and cannot begin with a dash.'
                elif lusername in RESERVED_NAMES or lusers:
                    errors['username'] = 'Username is already taken.'
                else:
                    source = self.session['register']['source']
                    uid = self.session['register']['uid']

                    if not email:
                        errors['email'] = 'You must have an email to use this service.'
                        email = None

                    user = models.User.get_or_insert(username,
                        role=rolechoice,
                        name=username,
                        lname=lusername,
                        email=email,
                        facebook_id=uid if source == models.USER_SOURCE_FACEBOOK else None,
                        google_id=uid if source == models.USER_SOURCE_GOOGLE else None,
                        token=base64.urlsafe_b64encode(os.urandom(30))[:32],
                    )
                    
                    if getattr(user, '%s_id' %source) != uid:
                        errors['username'] = 'Username is already taken.'
                    else:
                        del self.session['register']
                        self.populate_user_session(user)
                        counters.increment(counters.COUNTER_USERS)
                        if role == 'local':
                            self.add_message('Success', '%s, Welcome to Bunjil Forest Watch. Now create a new area that you want monitored.' %user)
                            self.redirect(webapp2.uri_for('new-area'))
                        else:
                            self.add_message('Success', '%s, Welcome new volunteer. Choose an area to follow.' %user)
                            self.redirect(webapp2.uri_for('main'))
                        return
            else:
                username = ''
                email = self.session['register']['email']

            self.render('register.html', {'username': username, 'email': email, 'errors': errors})
        else:
            self.redirect(webapp2.uri_for('main'))


class Logout(BaseHandler):
    def get(self):
        self.logout()
        self.redirect(webapp2.uri_for('main'))

class GoogleSwitch(BaseHandler):
    def get(self):
        self.logout()
        self.redirect(users.create_logout_url(webapp2.uri_for('login-google', protected_url = '/')))

class AccountHandler(BaseHandler): #superseded for now by user.html. no menu path to this.
    def get(self):
        if 'user' not in self.session:
            self.add_message('danger', 'You must log in to access your account.')
            self.redirect(webapp2.uri_for('main'))
            return

        u = cache.get_user(self.session['user']['name'])
        changed = False

        if 'callback' in self.request.GET:
            if 'access_token' in self.session:
                user_data = facebook.graph_request(self.session['access_token'])

                if u.facebook_id and user_data['id'] != u.facebook_id:
                    self.add_message('danger', 'This account has already been attached to a facebook account.')
                else:
                    u.facebook_id = user_data['id']
                    u.facebook_enable = True
                    u.facebook_token = self.session['access_token']
                    changed = True
                    self.add_message('success', 'Facebook integration enabled.')
        elif 'disable' in self.request.GET:
            disable = self.request.get('disable')
            if disable in models.USER_SOCIAL_NETWORKS or disable in models.USER_BACKUP_NETWORKS:
                setattr(u, '%s_enable' %disable, False)
                self.add_message('success', '%s posting disabled.' %disable.title())
                changed = True
        elif 'enable' in self.request.GET:
            enable = self.request.get('enable')
            if enable in models.USER_SOCIAL_NETWORKS or enable in models.USER_BACKUP_NETWORKS:
                setattr(u, '%s_enable' %enable, True)
                self.add_message('success', '%s posting enabled.' %enable.title())
                changed = True
        elif 'deauthorize' in self.request.GET:
            deauthorize = self.request.get('deauthorize')
            changed = True
            if deauthorize == models.USER_SOURCE_FACEBOOK:
                u.facebook_token = None
                u.facebook_enable = False
                self.add_message('success', 'Facebook posting deauthorized.')
            elif deauthorize == models.USER_SOURCE_TWITTER:
                u.twitter_key = None
                u.twitter_secret = None
                u.twitter_enable = None
                self.add_message('success', 'Twitter posting deauthorized.')
            elif deauthorize == models.USER_BACKUP_DROPBOX:
                u.dropbox_token = None
                u.dropbox_enable = None
                self.add_message('success', 'Dropbox backup deauthorized.')
            elif deauthorize == models.USER_BACKUP_GOOGLE_DOCS:
                utils.google_revoke(u.google_docs_token)
                u.google_docs_token = None
                u.google_docs_enable = None
                self.add_message('success', 'Google Docs backup deauthorized.')

        if changed:
            u.put()
            cache.set_keys([u])

        self.render('account.html', {
            'u': u,
            'show_navbar': True,
            #'backup':
                    'social': {
                'facebook': {
                    'auth_text': 'authorize' if not u.facebook_token else 'deauthorize',
                    'auth_url': facebook.oauth_url({'local_redirect': 'account'}, {'scope': 'publish_stream,offline_access'}) if not u.facebook_token else webapp2.uri_for('account', deauthorize='facebook'),
                    'enable_class': 'disabled' if not u.facebook_token else '',
                    'enable_text': 'enable' if not u.facebook_enable or not u.facebook_token else 'disable',
                    'enable_url': '#' if not u.facebook_token else webapp2.uri_for('account', enable='facebook') if not u.facebook_enable else webapp2.uri_for('account', disable='facebook'),
                    'label_class': 'warning' if not u.facebook_token else 'success' if u.facebook_enable else 'important',
                    'label_text': 'not authorized' if not u.facebook_token else 'enabled' if u.facebook_enable else 'disabled',
                },
                'twitter': {
                    'auth_text': 'authorize' if not u.twitter_key else 'deauthorize',
                    'auth_url': webapp2.uri_for('twitter', action='login') if not u.twitter_key else webapp2.uri_for('account', deauthorize='twitter'),
                    'enable_class': 'disabled' if not u.twitter_key else '',
                    'enable_text': 'enable' if not u.twitter_enable or not u.twitter_key else 'disable',
                    'enable_url': '#' if not u.twitter_key else webapp2.uri_for('account', enable='twitter') if not u.twitter_enable else webapp2.uri_for('account', disable='twitter'),
                    'label_class': 'warning' if not u.twitter_key else 'success' if u.twitter_enable else 'important',
                    'label_text': 'not authorized' if not u.twitter_key else 'enabled' if u.twitter_enable else 'disabled',
                },
            },
        })

    def post(self):
        changed = False
        u = cache.get_user(self.session['user']['name'])

        if 'settings' in self.request.POST:
            if 'email' in self.request.POST:
                email = self.request.get('email') 
                if not email:
                    email = None
                email = email.replace("'","").replace('"',"")    # Not XSS sanitised
                self.add_message('info', 'Email address updated.')
                
                if self.session['user']['email'] != email:
                    u.email = email
                    changed = True

        if 'social' in self.request.POST:
            self.add_message('success', 'Social media settings saved.')

            facebook_enable = 'facebook' in self.request.POST and self.request.get('facebook') == 'on'
            if u.facebook_enable != facebook_enable:
                u.facebook_enable = facebook_enable
                changed = True

        if changed:
            u.put()
            cache.set_keys([u])
            self.populate_user_session()

        self.redirect(webapp2.uri_for('account'))

class NewAreaHandler(BaseHandler):
    def get(self):
        
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Redirecting from protected url: ' + original_url)
            self.add_message('danger', 'You must log in to create a new area .')
            return self.redirect(users.create_login_url(original_url))
        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           
 
        if not registered:
            return self.redirect(webapp2.uri_for('register'))

        try:
            username = self.session['user']['name']
        except:
            logging.error('Should never get this exception')
            self.add_message('danger', 'You must log in to create a new area.')
            
            if 'user' not in self.session:
                self.redirect(webapp2.uri_for('main'))
                return

        latlng = self.request.headers.get("X-Appengine-Citylatlong") #user's location to center initial new map
        if latlng == None:
            logging.error('NewAreaHandler: No X-Appengine-Citylatlong in header')  
            latlng = '8.2, 22.2'
        self.render('new-area.html', {
                #'country': country,
                'latlng': latlng,
                'username': username,
            })    

    #@decorator.oauth_aware
    def post(self):
        name = self.request.get('name')
        descr = self.request.get('description')
        boundary_ft = self.request.get('boundary_ft')
        park_boundary_fc = None
        total_area = 0
        coords = []
        logging.debug('NewAreaHandler name: %s fusion:%s, description:%s', name, boundary_ft, descr)
        ftlink = 'https://www.google.com/fusiontables/DataSource?docid=none'
                
        if not eeservice.initEarthEngineService(): # we need earth engine now.
            self.add_message('danger', 'Sorry, Cannot contact Google Earth Engine right now to create your area. Please come back later')
            self.redirect(webapp2.uri_for('main'))
            return

        if not name:
            self.add_message('danger', 'Your area of interest needs a short and unique name. Please try again') #REDUNDANT This check now done in the browser.
            self.redirect(webapp2.uri_for('new-area'))      

        if self.session['areas_list']:
            if len(self.session['areas_list']) >= models.AreaOfInterest.MAX_AREAS:
                self.add_message('warning', 'Sorry, there is a quota of only %i areas per user.' %models.AreaOfInterest.MAX_AREAS)
                self.redirect(webapp2.uri_for('new-area'))
                return
        
        #check if area name is taken 
        for area_name in self.session['areas_list']:
            if name == area_name:
                self.add_message('danger', 'Sorry, there is already a protected area called %s. Please choose a different name and try again ' %name)
                self.redirect(webapp2.uri_for('new-area'))
                return
    
        if boundary_ft is None or boundary_ft is "":
            ### User Drew a Polygon ###
            try:
                coordinate_geojson_str = self.request.get('coordinates').decode('utf-8')
                logging.debug("NewAreaHandler() coordinate_geojson_str: %s ", coordinate_geojson_str)
                geojsonBoundary = geojson.loads(coordinate_geojson_str)
        
            except:
                return self.render('new-area.html', {
                    'username': name
                })    
           
            pts = []
            center_pt = []
            tmax_lat = -90
            tmin_lat = +90
            tmax_lon = -180
            tmin_lon = +180
            logging.debug("geojsonBoundary: %s",  geojsonBoundary)
            for item in geojsonBoundary['features']:
                if item['properties']['featureName']=="boundary":
                    pts=item['geometry']['coordinates']
                    #logging.info("pts: ", pts)
            
                    for lat,lon in pts:
                        gp = db.GeoPt(float(lat), float(lon))
                        coords.append(gp)
    
                        #get bounds of area.
                        if lat > tmax_lat: 
                            tmax_lat = lat
                        if lat < tmin_lat: 
                            tmin_lat = lat
                        if lon > tmax_lon:
                            tmax_lon = lon
                        if lon < tmin_lon: 
                            tmin_lon = lon
            for item in geojsonBoundary['features']:
                if item['properties']['featureName']=="mapview": # get the view settings to display the area.
                    zoom=item['properties']['zoom']
                    center_pt=item['geometry']['coordinates']
                    #logging.debug("zoom: %s, center_pt: %s, type(center_pt) %s", zoom, center_pt, type(center_pt) )
                    center = db.GeoPt(float(center_pt[0]), float(center_pt[1]))
                    #be good to add a bounding box too.

            maxlatlon = db.GeoPt(float(tmax_lat), float(tmax_lon))
            minlatlon = db.GeoPt(float(tmin_lat), float(tmin_lon))
            polypoints = []
            for geopt in coords:
                polypoints.append([geopt.lon, geopt.lat])
            cw_geom = ee.Geometry.Polygon(polypoints)
            ccw_geom = cw_geom.buffer(0, 1e-10) # force polygon to be CCW so search intersects with interior.
            feat = ee.Feature(ccw_geom, {'name': name, 'fill': 1})

            total_area = ccw_geom.area().getInfo()/1e6 #area in sq km

            park_boundary_fc = ee.FeatureCollection(feat)
          
        else:
            ### User Provided a Fusion Table ###
            #Test the fusion table
            #authenticate to fusion table API
            try:
                http = eeservice.EarthEngineService.credentials.authorize(httplib2.Http(memcache))
                service = build('fusiontables', 'v2', http=http)
                
                result = service.column().list(tableId=boundary_ft).execute(http)
                message = "Fusion Table Columns: "
                cols = result.get('items', [])
                
                geo_col_name= ""
                for c in cols:
                    message += c['name']
                    if c['type'] == 'LOCATION':
                        message += '[LOCATION]'
                        geo_col_name = c['name']
                    message +=  "; "
                logging.debug("results=%s, column names=%s", result, message)
                    
                # Fusion table EE operations tested at https://ee-api.appspot.com/b8dec39252c0eced49bb085f2b6fcdd4
                #make a convex hull and store the coordinates in db.model.AreoOfInterest.coords
    
                park_boundary_fc = ee.FeatureCollection(u'ft:' + boundary_ft, 'geometry')
                hull = park_boundary_fc.geometry().convexHull(10);
                coord_list = hull.coordinates().getInfo() #.buffer(0, 1e-10)  #(0, ee.ErrorMargin(10, 'units'), 'EPSG:3786')
                #print coord_list
                for lat,lng in coord_list[0]:
                    #print lat,lng
                    gp = db.GeoPt(float(lng), float(lat))
                    coords.append(gp)
                        
                bounds = park_boundary_fc.geometry().bounds(10)
                #centroid = bounds.centroid(10).getInfo()
                
                rectangle = bounds.coordinates().getInfo()
                #print 'rectangle', rectangle[0]
            
                #for p in rectangle[0]:
                #    print 'rectangle point', p
                maxlatlon = db.GeoPt(float(rectangle[0][2][1]), float(rectangle[0][2][0]))
                minlatlon =  db.GeoPt(float(rectangle[0][0][1]), float(rectangle[0][0][0]))
                #print 'maxlatlon', maxlatlon          
                #print 'minlatlon', minlatlon          
                
                centroid = bounds.centroid(10).coordinates().getInfo()
                #print 'centroid', centroid
                
                center = db.GeoPt(float(centroid[1]), float(centroid[0]))
                total_area = hull.area(10).getInfo()/1e6#area in sq km
                zoom = 12 # zoom will be calculated when the map is displayed.
                ftlink = 'https://www.google.com/fusiontables/DataSource?docid=' + boundary_ft

            except Exception, e : 
                logging.error("Exception reading fusion table %s", e)
                self.add_message('danger', "Error reading fusion table.  Exception: {0!s}".format(e)) 
                return self.redirect(webapp2.uri_for('new-area'))
            self.add_message('warning', "Fusion Tables are still experimental - Please reports any bugs") 

        def txn(user_key, area):
            user = db.get(user_key)
            user.areas_count += 1
            db.put([user, area])
            return user, area
        
        area_in_cells = total_area/LANSAT_CELL_AREA
        if total_area > (LANSAT_CELL_AREA * 6): # limit area to an arbitrary maximum size where the system breaks down.
            self.add_message('danger', 'Sorry, your area is too big (%d sq km = %d Landsat images). Try a smaller area.' %(total_area, area_in_cells))
            self.redirect(webapp2.uri_for('new-area'))
        
        decoded_name = name.decode('utf-8') #allow non-english area names.
        fc_info= json.dumps(park_boundary_fc.getInfo())
        area = models.AreaOfInterest(
                                    key_name=decoded_name, name=decoded_name, 
                                    description=self.request.get('description-what').decode('utf-8'), 
                                    description_why=self.request.get('description-why').decode('utf-8'), 
                                    description_who=self.request.get('description-who').decode('utf-8'), 
                                    description_how=self.request.get('description-how').decode('utf-8'), 
                                    coordinates=coords, boundary_fc= fc_info, ft_link=ftlink, ft_docid = boundary_ft,
                                    map_center = center, map_zoom = zoom, 
                                    max_latlon = maxlatlon,min_latlon = minlatlon, 
                                    owner=db.Key(self.session['user']['key']) )
        
        xg_on = db.create_transaction_options(xg=True)
        try:
            user, area = db.run_in_transaction_options(xg_on, txn, self.session['user']['key'], area)
            models.Activity.create(user, models.ACTIVITY_NEW_AREA, area.key())
            self.add_message('success', 'Created your new area of interest: %s covering about %d sq.km'  %(area.name, total_area ) )

            cache.clear_area_cache(user.key(), area.key())
            #clear_area_followers(area.key())
            cache.set(cache.pack(user), cache.C_KEY, user.key())
            
            counters.increment(counters.COUNTER_AREAS)
            
            self.populate_user_session()
            self.redirect(webapp2.uri_for('follow-area', username=user.name, area_name=area.name))
            return
        except Exception, e : 
            self.add_message('danger', "Error creating area.  Exception: {0!s}".format(e)) 
            self.redirect(webapp2.uri_for('new-area'))
            return

class UpdateAreaViewHandler(BaseHandler):
    def post(self, area_name):
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Redirecting from protected url: ' + original_url)
            return self.response.write('error no login')

        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           
 
        if not registered:
            return self.response.write('error not registered')
        try:
            username = self.session['user']['name']
        except:
            logging.error('Should never get this exception')
            return self.response.write('error exception')
            
            if 'user' not in self.session:
                return self.response.write('error user')
                return

        area = cache.get_area(None, area_name)

        if not area:
            return self.response.write('UpdateAreaViewHandler() - area not found')
            
        # if user does not own area or user is not admin - disallow
        if (area.owner.name != user.name) and (user.role != 'admin'):
            logging.error("Only the owner '{0!s}' of area '{1!s}' or admin can update an area.".format(area.owner, area.name))
            return self.response.write('error not owner')


        lat_str =  self.request.get('lat')
        if lat_str:
            lat = float(lat_str)
        else: 
            return self.response.write('error no lat param')
        
        lng_str =  self.request.get('lng')
        if lng_str:
            lng = float(lng_str)
        else: 
            return self.response.write('error no lng param ')

        zoom_str =  self.request.get('zoom')
        if zoom_str:
            lng = float(zoom_str)
        else: 
            return self.response.write('error no zoomparam ')

        area.map_zoom = int(zoom_str)
        area.map_center = db.GeoPt(float(lat_str), float(lng_str))

        try:
            db.put(area)
            cache.delete([cache.C_AREA %(username, area_name),  
                          cache.C_AREA %(None, area_name)])
            
            msg = 'Updated Area of interest: {0!s} Saved map view'.format(area_name)
            logging.info(msg)
            return self.response.write(msg)
        except Exception, e: 
            message = 'error Exception {0!s}'.format(e)
            logging.error(message)
            return self.response.write(message)

class UpdatedAreaSharedHandler(BaseHandler):
    def post(self, area_name, shared):
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Redirecting from protected url: ' + original_url)
            return self.response.write('error no login')

        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           
 
        if not registered:
            return self.response.write('error not registered')
        try:
            username = self.session['user']['name']
        except:
            logging.error('Should never get this exception')
            return self.response.write('error exception')
            
            if 'user' not in self.session:
                return self.response.write('error user')
                return

        area = cache.get_area(None, area_name)

        if not area:
            return self.response.write('UpdatedAreaSharedHandler() - area not found')
            
        # if user does not own area or user is not admin - disallow
        if (area.owner.name != user.name) and (user.role != 'admin'):
            logging.error("Only the owner '{0!s}' of area '{1!s}' or admin can update an area.".format(area.owner, area.name))
            return self.response.write('error not owner')

        if area.set_shared(shared) == 'error':
            logging.error('Shared, invalid value {0!s}'.format(shared))
            return self.response.write('error bad value {0!s}'.format(shared))
        try:
            db.put(area)
            if area.share != area.PUBLIC_AOI: # Now unlisted or private so get rid of all references in the cache.
                  cache.flush() 
            else:      
                cache.delete([cache.C_AREA %(username, area_name),  
                          cache.C_AREA %(None, area_name)])
            
            msg = 'Updated Area of interest: {0!s} is now {1!s}'.format(area_name, area.shared_str)
            logging.info(msg)
            return self.response.write(area.shared_str)
        except Exception, e: 
            message = 'error Exception {0!s}'.format(e)
            logging.error(message)
            return self.response.write(message)

 
class DeleteAreaHandler(BaseHandler):

    def get(self, area_name):
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Redirecting from protected url: ' + original_url)
            self.add_message('danger', 'You must log in to delete an area .')
            return self.redirect(users.create_login_url(original_url))
        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           
 
        if not registered:
            return self.redirect(webapp2.uri_for('register'))
        try:
            username = self.session['user']['name']
        except:
            logging.error('Should never get this exception')
            self.add_message('danger', 'You must log in to delete an area.')
            
            if 'user' not in self.session:
                self.redirect(webapp2.uri_for('main'))
                return

        cell_list = []

        area = cache.get_area(None, area_name)

        if not area:
            self.add_message('danger', 'Delete Area failed - Area not found.')
            logging.error('DeleteArea: Area not found! %s', area_name) # XSS should not return area to client.
            return self.error(404)
        else:
            cell_list = area.CellList()
            observations =    {} 
            if area.ft_docid is not None and len(area.ft_docid) <> 0:
                area.isfusion = True
            else:
                area.isfusion = False

        # if user does not own area or user is not admin - disallow
       
         
        if (area.owner.name != user.name) and (user.role != 'admin'):
            logging.error('Only the owner of an area or admin can delete an area %s %s', area.owner, user)
            self.add_message('danger', "Only the owner of an area or admin can delete an area. owner:'{0!s}' user:'{1!s}'".format(area.owner, user))
            
            geojson_area = area.geojsonArea()
 
            return self.redirect(webapp2.uri_for('view-area', area_name=area_name), {
                'username': user.name,
                'area_json' : geojson_area,
                'area': area,
                'show_navbar': True,
                'show_delete':false,
                'is_owner': false,
                'celllist':json.dumps(cell_list), # to be replaced by jsonarea
                'area_followers': area_followers,
                'obslist': json.dumps(observations)
            })
        
        #remove area from other user's area_following lists.        
        area_followers_index = cache.get_area_followers(area_name)
        #print 'area_followers_index ', area_followers_index
        def txn(user_key, area, area_followers_index):
            user = db.get(user_key)
            user.areas_count -= 1 # decrement the user's area count.
            db.put(user)
            for cell_key in area.cells:
                db.delete(cell_key)
            if  area_followers_index is not None:
                db.delete(area_followers_index.key())
            db.delete(area.key())
            return

        xg_on = db.create_transaction_options(xg=True)
        owner_key = area.owner.key()
        logging.debug('owner_key' , owner_key)
        
        try:
            db.run_in_transaction_options(xg_on, txn, owner_key, area, area_followers_index)
            
            cache.clear_area_cache(user.key(), area.key())
            cache.clear_area_followers(area.key())
            cache.set(cache.pack(user), cache.C_KEY, user.key())
            
            models.Activity.create(user, models.ACTIVITY_DELETE_AREA, area.key())
            counters.increment(counters.COUNTER_AREAS, -1)
            
            self.populate_user_session()
            self.add_message('info', 'Deleted area of interest: {0!s}'.format(area_name))
            self.redirect(webapp2.uri_for('main'))
            #raise Exception('test', 'exception')
            return
        except Exception, e: 
            message = 'Sorry, Could not delete area: Exception {0!s}'.format(e) #XSS safe
            self.add_message('danger', message)
            logging.error(message)
            self.redirect(webapp2.uri_for('view-area', area_name=area.name))
            return
        

'''
SelectCellHandler is called by Ajax when a user clicks on a Landsat Cell in the browser.
This toggles the 'monitored' flag in the cell object in the datastore and flushes the cell and area cache.
TODO: Add the latest observation date (if known) to the return.
'''
class SelectCellHandler(BaseHandler):
    def get(self, area_name, celldata):
        # get cell info in request.
        self.populate_user_session()
        cell_feature = json.loads(celldata)
        #print 'cell_feature ', cell_feature
        path = cell_feature['properties']['path']
        row = cell_feature['properties']['row']
        displayAjaxResponse = 'Cell {0:d} {1:d}'.format(path, row)

        area = cache.get_area(None, area_name)
        username = self.session['user']['name']

        if not area or area.owner.name != username:
            logging.info('selectCell() not area owner')
            response = {'error':'Only area owner can select cell for monitoring'}
            return self.response.write( json.dumps(response))

        #build cell info in response.
        cell = cache.get_cell(path, row, area_name)
        #print "cell", cell 
        if cell is not None:
            #Update the followed flag.
            if cell.monitored == True:
                cell.monitored = False
            else:
                cell.monitored = True
            db.put(cell)
            cell_dict = cell.Cell2Dictionary()
            cache.delete([cache.C_CELL_KEY %cell.key(),
                          cache.C_CELL %(path, row, area_name), 
                          cache.C_CELLS %(cell.aoi)])
            cell_dict['monitoredCount'] = area.CountMonitoredCells()
            self.response.write(json.dumps(cell_dict))
        else:
            logging.error('Selected Cell does not exist %d %d', path, row)
            self.response.write( {'danger':'Not a cell'})
        return
    
    def post(self):
        #print 'SelectCellHandler post'
        name = self.request.get('name')
        descr = self.request.get('description')
        #logging.debug('SelectCellHandler name: %s description:%s', name, descr)
        
        try:
            coordinate_geojson_str = self.request.get('coordinates').decode('utf-8')
            #logging.debug("SelectCellHandler() coordinate_geojson_str: ", coordinate_geojson_str)
            geojsonBoundary = geojson.loads(coordinate_geojson_str)
    
        except:
            return self.render('view-area.html', {
                'username': name
            })    
        
        self.render('view-area.html')


class NewJournal(BaseHandler):
    def get(self):
        self.render('new-journal.html')

    def post(self):
        name = self.request.get('name')

        if len(self.session['journals']) >= models.Journal.MAX_JOURNALS:
            self.add_message('danger', 'Only %i journals allowed.' %models.Journal.MAX_JOURNALS)
        elif not name:
            self.add_message('danger', 'Your journal needs a name.')
        else:
            journal = models.Journal(parent=db.Key(self.session['user']['key']), name=name)
            for journal_url, journal_name, journal_type in self.session['journals']:
                if journal.name == journal_name:
                    self.add_message('danger', 'You already have a journal called %s.' %name)
                    break
            else:
                def txn(user_key, journal):
                    user = db.get(user_key)
                    user.journal_count += 1
                    db.put([user, journal])
                    return user, journal

                journal.journal_type = "journal"
                user, journal = db.run_in_transaction(txn, self.session['user']['key'], journal)
                cache.clear_journal_cache(user.key())
                cache.set(cache.pack(user), cache.C_KEY, user.key())
                self.populate_user_session()
                counters.increment(counters.COUNTER_AREAS)
                models.Activity.create(user, models.ACTIVITY_NEW_JOURNAL, journal.key())
                self.add_message('success', 'Created your journal %s.' %name)
                self.redirect(webapp2.uri_for('new-entry', username=self.session['user']['name'], journal_name=journal.name))
                return

        self.render('new-journal.html')

    
class ViewArea(BaseHandler):
        
    def get(self, area_name):
        
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Redirecting from protected url: ' + original_url)
            self.add_message('danger', 'You must log in to view areas.')
            return self.redirect(users.create_login_url(original_url))
        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           
 
        if not registered:
            return self.redirect(webapp2.uri_for('register'))
        try:
            username = self.session['user']['name']
        except:
            logging.error('Should never get this exception')
            self.add_message('danger', 'You must log in to see this page.')
            
            if 'user' not in self.session:
                self.redirect(webapp2.uri_for('main'))
                return

        area = cache.get_area(None, area_name)
        if not area or (area.share == area.PRIVATE_AOI and area.owner.name != user.name and not users.is_current_user_admin()):
            self.add_message('danger', "Area not found!")
            logging.error('ViewArea: Area not found! %s', area_name)
            self.redirect(webapp2.uri_for('main'))
        else:
            if (area.share == area.PRIVATE_AOI and area.owner.name != user.name and users.is_current_user_admin()):
                self.add_message('warning', "Only admin and owner can view this area: '{0:s}'!".format(area_name))
    
            # Make a list of the cells that overlap the area with their path, row and status. 
            #This may be an empty list for a new area.
            
            cell_list = []        
            cell_list = area.CellList()
            observations =    {} 
            
            if area.ft_docid is not None and len(area.ft_docid) <> 0:
                #print 'fusion table DocId', area.ft_docid
                area.isfusion = True
            else:
                #print 'no fusion table in area', area.ft_docid, area.boundary_fc
                area.isfusion = False
    
            area_followers_index = cache.get_area_followers(area_name)
            #print 'area_followers ', area_followers_index, 
            if area_followers_index:
                area_followers = area_followers_index.users
            else:
                area_followers = []
            
            #print 'user ', user.name, user.role
            
            if (area.owner.name == user.name):
                is_owner = True
                show_delete = True
            else:
                is_owner = False
                show_delete = False
                
            if users.is_current_user_admin():
                show_delete = True
            
            geojson_area = area.geojsonArea()
             
            self.render('view-area.html', {
                'username': user.name,
                'area_json' : geojson_area,
                'area': area,
                #'boundary_ft' : area.boundary_ft,
                'show_navbar': True,
                'show_delete':show_delete,
                'is_owner': is_owner,
                'celllist':json.dumps(cell_list), # to be replaced by jsonarea
                'area_followers': area_followers,
                'obslist': json.dumps(observations)
            })
            

'''
Use Earth Engine to work out which cells belong to an AreaOfInterest.
Store the result in aoi.cells.
Each cell identified by landsat Path and Row.
'''
class GetLandsatCellsHandler(BaseHandler):
    #This handler responds to Ajax request, hence it returns a response.write()

    def get(self, area_name):
        area = cache.get_area(None, area_name)
        if not area or area is None:
            logging.info('GetLandsatCellsHandler - area %s not found', area_name)
            self.error(404)
            return
        cell_list = area.CellList()
        result ='success'
        if len(cell_list)== 0:
            
            #logging.debug('GetLandsatCellsHandler area.name: %s type: %d area.key(): %s', area.name, type(area), area.key())
            if not eeservice.initEarthEngineService(): # we need earth engine now.
                result = 'danger'
                reason = 'Sorry, Cannot contact Google Earth Engine right now to create visualization. Please come back later'
                self.add_message('danger', reason)
                logging.error(reason)
                getCellsResult = {'result': result, 'reason': reason}
                self.response.write(json.dumps(getCellsResult))
                return
            else:       
                eeservice.getLandsatCells(area)
                area = cache.get_area(None, area_name) # refresh the cache as it has been updated by getLandsatCells(). #TODO test this works
                cell_list = area.CellList()
                monitored_count = sum(c['monitored'] == 'true' for c in cell_list)
                reason = 'Your area is covered by {0:d} Landsat Cells of which {1:d} were selected for monitoring'.format(len(area.cells), monitored_count)
                self.add_message('success',reason)
                logging.debug(reason)
        else:
            reason = 'calculate previously'
        getCellsResult = {'result': result, 'reason': reason, 'cell_list': cell_list, 'monitored_count': monitored_count }
        self.response.write(json.dumps(getCellsResult))
        return 

class LandsatOverlayRequestHandler(BaseHandler):  #'new-landsat-overlay'
    #This handler responds to Ajax request, hence it returns a response.write()

    def get(self, area_name, action, satelite, algorithm, latest, **opt_params):
        area = cache.get_area(None, area_name)

        if not area:
            returnval = {}
            returnval['result'] = "error"
            returnval['reason'] = 'LandsatOverlayRequestHandler: Invalid area ' + area_name
            self.add_message('danger', returnval['reason'] )
            logging.error(returnval['reason'])
            self.response.write(json.dumps(returnval))
            return

        if not self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            logging.info('LandsatOverlayRequestHandler(): Ajax request expected ')
        
        logging.debug("LandsatOverlayRequestHandler area:%s, action:%s, satellite:%s, algorithm:%s, latest:%s", area_name, action, satelite, algorithm, latest) #, opt_params['path'], opt_params['row'])
        if not area:
            logging.info('LandsatOverlayRequestHandler - bad area returned %s, %s', area, area_name)
            self.error(404)
            return
    
        poly = []
        for geopt in area.coordinates:
            poly.append([geopt.lon, geopt.lat])
       
        if not eeservice.initEarthEngineService(): # we need earth engine now.
            returnval = {}
            returnval['result'] = "error"
            returnval['reason'] = 'Sorry, Cannot contact Google Earth Engine right now to create visualization. Please come back later'
            self.add_message('danger', returnval['reason'] )
            logging.error(returnval['reason'])
            self.response.write(json.dumps(returnval))
        
        map_id = eeservice.getLandsatOverlay(poly, satelite, algorithm, latest, opt_params)
        if not map_id:
            returnval = {}
            returnval['result'] = "error"
            returnval['reason'] = 'Sorry, Cannot creat overlay.  Google Earth Engine did not provide a map_id. Please come back later'
            self.add_message('danger', returnval['reason'] )
            logging.error(returnval['reason'])
            self.response.write(json.dumps(returnval))
            return
        
        #Save observation - will it work if no path or row?
        if 'path' in opt_params and 'row' in opt_params:
            path = int(opt_params['path'])
            row =  int(opt_params['row'])
            
            #logging.debug("LandsatOverlayRequestHandler() path %s, row %s", path, row)
            cell = cache.get_cell(path, row, area_name)
            if cell is not None:
                #captured_date = datetime.datetime.strptime(map_id['date_acquired'], "%Y-%m-%d")
                obs = models.Observation(parent = cell, image_collection = map_id['collection'], captured = map_id['capture_datetime'], image_id = map_id['id'], 
                                         rgb_map_id = map_id['mapid'], rgb_token = map_id['token'],  algorithm = algorithm)
            else:
                returnval = {}
                returnval['result'] = "error"
                returnval['reason'] = 'LandsatOverlayRequestHandler - cache.get_cell error'
                self.add_message('danger', returnval['reason'] )
                logging.error(returnval['reason'])
                self.response.write(json.dumps(returnval))
 
        else:
            obs = models.Observation(parent = area, image_collection = map_id['collection'], captured = map_id['capture_datetime'], image_id = map_id['id'], 
                                         rgb_map_id = map_id['mapid'], rgb_token = map_id['token'],  algorithm = algorithm)
            
        db.put(obs)
        ovl = models.Overlay(parent = obs, 
                                 map_id = map_id['mapid'], 
                                 token = map_id['token'],
                                 overlay_role = 'special',
                                 algorithm = algorithm)
        
        db.put(ovl)  #Do first to create a key.
        obs.overlays.append(ovl.key())
        db.put(obs)  #TODO put inside a tx
        cache.set_keys([obs, ovl])

        #logging.info("map_id %s", map_id) logging.info("tile_path %s",area.tile_path)
        returnval = ovl.Overlay2Dictionary()
        returnval['result'] = "success"
        returnval['reason'] = "LandsatOverlayRequestHandler() created " + ovl.overlay_role + " " + ovl.algorithm + " overlay."
        logging.debug(returnval['reason']) 
        #self.populate_user_session() - no user in Ajax call.
        self.response.write(json.dumps(returnval))
        
        

'''
CreateOverlayHandler() create a new overlay and appends it to the observation
        parameters: 
            observation key, 
            algorithm, 
            username is not used
'''
class CreateOverlayHandler(BaseHandler):
    #This handler responds to Ajax request, hence it returns a response.write()

    def get(self, username, obskey, role, algorithm):
        #user = cache.get_user(username) #not used.
        obs = cache.get_by_key(obskey) #FIXME make type safe for security.
        returnval = {}
        
        if not obs:
            returnval['result'] = "error"
            returnval['reason'] = "GetObservationHandler() -  bad observation key in url"
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))
        
        logging.debug("CreateOverlayHandler() Creating %s %s visualization of image %s from collection :%s", role, algorithm, obs.image_id, obs.image_collection)
        
        eeservice.initEarthEngineService() # we need earth engine now.
        if not eeservice.initEarthEngineService(): # we need earth engine now. logging.info(initstr)        
            returnval['result'] = "error"
            returnval['reason'] = "CreateOverlayHandler() - Cannot contact Google Earth Engine to generate overlay"
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))
           
        if   role == 'latest':
           map_id = eeservice.getLandsatImageById(obs.image_collection,  obs.image_id, algorithm)
        elif   role == 'special':
           map_id = eeservice.getLandsatImageById(obs.image_collection,  obs.image_id, algorithm)
        elif role == 'prior':
            map_id = eeservice.getPriorLandsatOverlay(obs)
        else:
            returnval['result'] = "error"
            returnval['reason'] = "CreateOverlayHandler() - Unknown role " + role + " (use 'latest' or 'prior'). "
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))

        if not map_id:
            returnval['result'] = "error"
            returnval['reason'] = "CreateOverlayHandler() Earth Engine did not return Overlay"
            logging.error(returnval['reason']) 
            #self.populate_user_session()
            self.response.write(json.dumps(returnval))
            return
      
        #captured_date = datetime.datetime.strptime(map_id['date_acquired'], "%Y-%m-%d")
        ovl = models.Overlay(parent   = obs,
                             map_id   = map_id['mapid'], 
                             token    = map_id['token'],
                             overlay_role = role,  #is it safe to assume?
                             algorithm = algorithm)

        #obs.captured = map_id['capture_datetime'] #we already  had this?
        
        db.put(ovl)  #Do first to create a key.
        obs.overlays.append(ovl.key())
        db.put(obs)  #TODO put inside a tx
        cache.set_keys([obs, ovl])

        returnval = ovl.Overlay2Dictionary()
        returnval['result'] = "success"
        returnval['reason'] = "CreateOverlayHandler() added " + role + " " + algorithm + " overlay"
        logging.debug(returnval['reason']) 
        
        #self.populate_user_session()
        self.response.write(json.dumps(returnval))

# if Image is known.
class UpdateOverlayHandler(BaseHandler):
    #This handler responds to Ajax request, hence it returns a response.write()

    def get(self, username, ovlkey, algorithm):
        #user = cache.get_user(username) #not used.
        
        ovl = cache.get_by_key(ovlkey) #FIXME make type safe for security.
        returnval = {}
        
        if not ovl:
            returnval['result'] = "error"
            returnval['reason'] = "UpdateOverlayHandler Could not find Image"
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))

        obs = ovl.parent();
        if not obs:
            returnval['result'] = "error"
            returnval['reason'] = "UpdateOverlayHandler() - overlay has not parent observation"
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))
        
        logging.debug("UpdateOverlayHandler() visualization of image %s from collection :%s", obs.image_id, obs.image_collection)
        
        eeservice.initEarthEngineService() # we need earth engine now.
        if not eeservice.initEarthEngineService(): # we need earth engine now. logging.info(initstr)        
            returnval['result'] = "error"
            returnval['reason'] = "UpdateOverlayHandler() - Cannot contact Google Earth Engine to update overlay"
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))
   
      
        ovl.algorithm = algorithm # shouldn't change?
        
        if   ovl.overlay_role == 'latest':
            map_id = eeservice.getLandsatImageById(obs.image_collection,  obs.image_id, ovl.algorithm)
        elif  ovl.overlay_role == 'special':
           map_id = eeservice.getLandsatImageById(obs.image_collection,  obs.image_id, algorithm)    
        elif ovl.overlay_role == 'prior':
            map_id = eeservice.getPriorLandsatOverlay(obs)
        else:
            returnval['result'] = "error"
            returnval['reason'] = "UpdateOverlayHandler() - Unknown role " + ovl.overlay_role + "(use 'latest' or 'prior')."
            logging.error(returnval['reason']) 
            return self.response.write(json.dumps(returnval))
        
        if not map_id:
            returnval['result'] = "error"
            returnval['reason'] = "UpdateOverlayHandler Could not find Image"
            logging.error(returnval['reason']) 
            #self.populate_user_session()
            self.response.write(json.dumps(returnval))
            return
      
        ovl.map_id = map_id['mapid']
        ovl.token  = map_id['token']
        
        db.put(ovl) 
        cache.set_keys([ovl])
        
        returnval = ovl.Overlay2Dictionary()
        returnval['result'] = "success"
        returnval['reason'] = "UpdateOverlayHandler() updated " + ovl.overlay_role + " " + ovl.algorithm + " overlay"
        logging.debug(returnval['reason']) 
        
        self.populate_user_session()
        self.response.write(json.dumps(returnval))


'''
ObservationTaskHandler() when a user clicks on a link in an obstask email they come here to see the new image.
'''
class ObservationTaskHandler(BaseHandler):
    
    def get(self, username, task_name):
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Cannot access protected url' + original_url)
            return self.redirect(users.create_login_url(original_url))
        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           

        task_owner = cache.get_user(username) #user who owns the task is passed in url (not the task object.

        if task_owner is None:
            self.add_message('danger', 'Sorry, invalid task owner: ' + username)
            self.redirect(webapp2.uri_for('main'))
            return
         
        task = cache.get_task(task_name)  #FIXME Must be typesafe
        if task is not None:
            if current_user.nickname != task.assigned_owner.name:
                self.add_message('info', "You {0!s} are not assigned to this task. Task assigned to {1!s}".format(current_user.nickname , task.assigned_owner))
            area = task.aoi 
            cell_list = area.CellList()  
            resultstr = "New Task for {0!s} to check area {1!s}".format(user.name, area.name.encode('utf-8') )
            #self.add_message('success', resultstr)
            debugstr = resultstr + " task: " + str(task.key()) + " has " + str(len(task.observations)) + "observations"
            obslist = []
            for obs_key in task.observations:
                obs = cache.get_by_key(obs_key) 
                if obs is not None:
                    obslist.append(obs.Observation2Dictionary()) # includes a list of precomputed overlays
                else:
                    logging.error("Missing Observation from cache")
            
            geojson_area = area.geojsonArea()
            self.populate_user_session(user)
            self.render('view-obstask.html', {
                'username':  self.session['user']['name'],
                'area_json' : geojson_area,
                'area': area,
                'owner': task_owner,
                'show_delete':False,
                'task': task,
                'show_navbar': True,
                #'area_followers': area_followers,
                'obslist': json.dumps(obslist),
                'celllist':json.dumps(cell_list)
            })
  
        else:
            resultstr = "Sorry, Task not found. ObservationTaskHandler: key {0!s}".format(task_name)
            self.add_message('danger', resultstr)
            logging.error(resultstr)
            self.response.write(resultstr)
   

'''
checkAreaForNew() is a function to check if any new images for the specified area. 
Called by CheckNewAllAreasHandler() and CheckNewAreaHandler()
returns a HTML formatted string
'''
def checkAreaForNew(area, hosturl):
    area_followers  = models.AreaFollowersIndex.get_by_key_name(area.name, area) 
    linestr = u'<h2>Area:<b>{0!s}</b></h2>'.format(area.name)
    obstask_cachekeys = []
 
    if area_followers:
        linestr += u'<p>Monitored cells ['
        new_observations = []
        for cell_key in area.cells:
            cell = cache.get_cell_from_key(cell_key)
            if cell is not None:
                if cell.monitored:
                    linestr += u'({0!s}, {1!s}) '.format(cell.path, cell.row)
                    obs = eeservice.checkForNewObservationInCell(area, cell, "LANDSAT/LC8_L1T_TOA")
                    if obs is not None :
                        linestr += 'New '
                        new_observations.append(obs.key())
            else:
                logging.error (u"CheckNewAreaHandler() in area:{0!s} no cell returned from key:{1!s} ".format(area.name, cell_key))
        if new_observations == []:
            linestr += u'] Area has no monitored cells!</p>'
        else:
            linestr += u']</p>'
            
        # send each follower of this area an email with reference to a task.
        if new_observations:
            new_task = models.ObservationTask(aoi=area.key(), observations=new_observations, aoi_owner=area.owner, share=area.share, status="open") # always select the first follower.
            priority = 0
            for user_key in area_followers.users:
                user = cache.get_user(user_key)  
                new_task.assigned_owner = user
                new_task.name = user.name + u"'s task with priority " + str(priority) + " for " + area.name
                new_task.priority = priority
                priority += 1
                db.put(new_task)
                mailer.new_image_email(new_task, hosturl) 
                linestr += "<p>Created task with " + str(len(new_observations)) +" observations.</p>"
                
                taskurl = "/obs/" + user.name + "/" + str(new_task.key())
                linestr += u'<a href=' + taskurl + ' target="_blank">' + taskurl.encode('utf-8') + '</a>'
            

            linestr += u'<ul>'
            for ok in new_observations:
                o = cache.get_by_key(ok)
                #clear_obstasks_cache(o)
                linestr += u'<li>image_id: ' + o.image_id + u'</li>' 
            linestr += u'</ul>'

            cache.flush() # TODO To scale, will need a better strategy to just flush applicable items.
        else:
            linestr += u"<ul><li>No new observations found.</li></ul>"
    else:
        linestr += u'Area has no followers. Skipping check for new observations.<br>'.format(area.name)
    
    logging.debug(linestr)
    return linestr


'''
CheckNewAllAreasHandler() looks at each subscribed area of interest.
It checks each monitored cell to see if there is a new image in EE since the last check.
This is called by the cron task, but may be kicked of by admin.
Not: Includes private and unlisted areas in the check. So the tasks lists must be filtered.
'''
           
class CheckNewAllAreasHandler(BaseHandler):
    def get(self):
        logging.info("Cron CheckNewAllAreasHandler check-new-images")
        initstr = u"<h1>Check areas for new observations</h1>"
        initstr += u"<p>The scheduled task is looking for new images over all areas of interest</p>"
        initstr += u"<p>The area must have at least one cell selected for monitoring and at least one follower for a observation task to be created.</p>"
        initstr += u"<p>If an observation task is created, the first follower of the area receives an email with an observation task.</p>"
        
        if not eeservice.initEarthEngineService(): # we need earth engine now. logging.info(initstr)        
            initstr =u'CheckNewAllAreasHandler: Sorry, Cannot contact Google Earth Engine right now to create your area. Please come back later'
            self.response.write(initstr) 
            return
        returnstr = initstr

        all_areas = cache.get_all_areas() # includes unlisted and private areas.
        for area in all_areas:
            returnstr += checkAreaForNew(area,  self.request.headers.get('host', 'no host'))

        self.response.write(returnstr.encode('utf-8')) 

'''
CheckNewAreaHandler() looks at a single area of interest and checks each monitored cell.
It checks if there is a new image in EE since the last check.
This function is called by admin only.
#TODO: It could be refactored with CheckAllAreasHnadler.
'''
       
class CheckNewAreaHandler(BaseHandler):
       def get(self, area_name):
        
        area = cache.get_area(None, area_name)
        logging.debug('CheckNewAreaHandler check-new-area-images for area_name %s %s', area_name, area)
        if not area:
            area = cache.get_area(None, area_name)
            logging.error('CheckNewAreaHandler: Area not found! %s', area_name)
            return self.error(404)

        initstr = u"<h1>Check area for new observations</h1>"
        initstr += u"<p>The area must have at least one cell selected for monitoring and at least one follower for a observation task to be created.</p>"
        initstr += u"<p>If an observation task is created, the first follower of the area receives an email with an observation task.</p>"
        
        if not eeservice.initEarthEngineService(): # we need earth engine now. logging.info(initstr)        
            initstr =u'CheckNewAreaHandler: Sorry, Cannot contact Google Earth Engine right now to create your area. Please come back later'
            #self.add_message('danger', initstr)
            return self.response.write(initstr) 
            

        returnstr = initstr + checkAreaForNew(area)
        return self.response.write(returnstr.encode('utf-8')) 

'''
MailTestHandler() - This handler sends a test email 
'''
class MailTestHandler(BaseHandler):

  def get(self):
#     username = []
#     if 'user' in self.session:
#         areas = cache.get_areas(db.Key(self.session['user']['key'])) # areas user created
#         self.populate_user_session() #Only need to do this when areas, journals  or followers change
#         username = "myemail@gmail.com"
#     
#     else:
#         username = "myotheremail@gmail.com"
    
    user = cache.get_user(self.session['user']['name'])
    tasks = models.ObservationTask.all().order('-created_date').fetch(2)
    #mailer.new_image_email(user)
    if not tasks:
        return self.handle_error("No tasks to test mailer")
    resultstr = mailer.new_image_email(tasks[0], self.request.headers.get('host', 'no host') )
    
    self.response.write( resultstr)        

class ViewJournal(BaseHandler):
    #FIXME: Pagination for view-journals does not work.
    def get(self, username, journal_name):
        page = int(self.request.get('page', 1))
        journal= cache.get_journal(username, journal_name)

        logging.info('ViewJournal journal_name %s %s', journal_name, journal)
        
        if not journal or username != self.session['user']['name']:
            self.error(404)
            return

        if not journal:
            self.error(404)
        else:
            self.render('view-journal.html', {
                'username': username,
                'journal': journal,
                'entries': cache.get_entries_page(username, journal_name, page, journal.key()),
                'page': page,
                'show_navbar': True,
                'pagelist': utils.page_list(page, journal.pages),
            })

    
class ViewObservationTasksHandler(BaseHandler):
    
    def ViewTasks(self):
        page = int(self.request.get('page', 1))
        u = self.request.get('user2view')
        a = self.request.get('area_name')
        user2view  = None if u == "" or u == "None" else u
        area_name = None if a == ""or a == "None"  else a

        # get the logged in user.
        current_user = users.get_current_user()
        if  not current_user:
            abs_url  = urlparse(self.request.uri)
            original_url = abs_url.path
            logging.info('No user logged in. Redirecting from protected url: ' + original_url)
            return self.response.write('error no login')

        user, registered = self.process_credentials(current_user.nickname(), current_user.email(), models.USER_SOURCE_GOOGLE, current_user.user_id())           
        
        #if 'user' not in self.session:
        #        return self.response.write('error user')
 
        if not registered:
            return self.response.write('error not registered')
        try:
            username = self.session['user']['name']
        except:
            logging.error('Should never get this exception')
            return self.response.write('error exception')
            
        if area_name:
            area = cache.get_area(None, area_name)
            if area:
                # if user does not own area or user is not admin - disallow
                if (area.shared_str == 'private') and (area.owner.name != user.name) and (user.role != 'admin'):
                    message = "Cannot show tasks for non-public area: '{0!s}', except to owner: '{1!s}'.".format(area_name, area.owner)
                    logging.error(message)
                    return self.response.write(message)
            else:
                logging.error('Requested area {0!s} not found '.format(area_name))
                return self.response.write('Requested area not found') # Don't print area to avoid XSS attack.
                
        tasks  = cache.get_obstasks_keys(user2view, area_name) # list of all task keys
        obstasks = cache.get_obstasks_page(page, user2view, area_name) # rendered page of tasks #TODO is it needed? 
        
        if obstasks == None or len(obstasks)  == 0 :
            logging.info('ViewObservationTasksHandler - no tasks!') 
            obstask = None
            obstasks = None
            pages =  0
            tasks = None
        else:
            logging.debug('ViewObservationTasks: user:%s, area:%s, page:%d', user2view, area_name, page) # Move here to XSS sanitise user2view
            pages = len(tasks) / models.ObservationTask.OBSTASKS_PER_PAGE
            if pages < 1:
                pages = 1

            if page < 1 or page > pages:
                self.error(404)
                return
            
            obstask = cache.get_task(tasks[0]) # template needs this to get listurl to work?
            logging.info('ViewObservationTasksHandler showing %d tasks for user %s', len(obstasks), user2view) 
        
        user = cache.get_user(self.session['user']['name']) #user from current session
        
        
        self.render('view-obstasks.html', {
            'username': user,  #logged in user
            'user2view': user2view,  # or none
            'area_name': area_name, # or none
            'obstask': obstask,
            'obstasks': obstasks,
            'tasks' :  tasks,
            'pages' : pages,
            'page': page,
            'show_navbar': True,
            'filter' : filter, # not used
            'pagelist': utils.page_list(page, pages)
        })
    '''
    def ViewObservationTasksForArea(self, area_name):
        #page = int(self.request.get('page', 1))
        logging.debug('ViewObservationTasksForArea')
        return self.ViewTasks(None, area_name)

    def ViewObservationTasksForUser(self, user2view):
        #page = int(self.request.get('page', 1))
        logging.debug('ViewObservationTasksForUser')
        return self.ViewTasks(user2view, None)
    '''
            
    def ViewObservationTasksForAll(self):       
        #page = int(self.request.get('page', 1))
        logging.debug('ViewObservationTasksForAll')
        return self.ViewTasks()
   
class AboutHandler(BaseHandler):
    def get(self):
        self.render('about.html' , {
                'show_navbar': True
            })

class DonateHandler(BaseHandler):
    def get(self):
        self.render('donate.html')
        
class ActivityHandler(BaseHandler):
    def get(self):
        self.render('activity.html', {'activities': cache.get_activities()})

class FeedsHandler(BaseHandler):
    def get(self, feed):
        token = self.request.get('token')
        xml = cache.get_feed(feed, token)

        if not xml:
            self.error(404)
        else:
            self.response.out.write(xml)

class UserHandler(BaseHandler):
    def get(self, username):
        u = cache.get_user(username)

        if not u:
            self.error(404)
            return

        journals = cache.get_journals(u.key())
        #logging.info ("journals %s", journals)
        areas= cache.get_areas(u.key())
        #logging.info ("areas %s", areas)
        activities = cache.get_activities(username=username)
        following = cache.get_following(username)
        followers = cache.get_followers(username)
        #following_areas= cache.get_following_areas(username)
        
        #logging.info ("following %s, followers %s", following, followers)
        
        if 'user' in self.session:
            is_following = username in cache.get_following(self.session['user']['name'])
            thisuser = self.session['user']['name'] == u.name
        else:
            logging.debug("%s has no followers", username)
            is_following = False
            thisuser = False 

        #logging.info ("u is %s", u)
        
        self.render('user.html', {
            'u': u,
            'journals': journals,
            'activities': activities,
            'following': following,
            'followers': followers,
            'is_following': is_following,
            'thisuser': thisuser, # True if user being shown is thisuser
            'areas': areas,
             'show_navbar': True,
            })
"""
        self.render('user.html', {
            'u': u,
            'journals': journals,
            'activities': activities,
            'following': following,
            'followers': followers,
            'is_following': is_following,
            'thisuser': thisuser
            #'areas': areas
        })
"""


class ViewJournalsHandler(BaseHandler):
    def get(self, username):
        u = cache.get_user(username)

        if not u:
            self.error(404)
            return

        journals = cache.get_journals(u.key())
        #logging.info ("journals %s", journals)
        areas= cache.get_areas(u.key())
        #logging.info ("areas %s", areas)
        activities = cache.get_activities(username=username)
        following = cache.get_following(username)
        followers = cache.get_followers(username)
        #following_areas= cache.get_following_areas(username)
        
        #logging.info ("following %s, followers %s", following, followers)
        
        if 'user' in self.session:
            is_following = username in cache.get_following(self.session['user']['name'])
            thisuser = self.session['user']['name'] == u.name
        else:
            logging.debug("%s has no followers", username)
            is_following = False
            thisuser = False 

        #logging.info ("u is %s", u)
        
        self.render('view-journals.html', {
            'u': u,
            'journals': journals,
            'activities': activities,
            'following': following,
            'followers': followers,
            'is_following': is_following,
            'thisuser': thisuser, # True if user being shown is thisuser
            'areas': areas,
             'show_navbar': True,
            })

class FollowHandler(BaseHandler):
    
    def get(self, username, area):
        user = cache.get_user(username)
        if not user or 'user' not in self.session:
            self.error(404)
            return

        thisuser = self.session['user']['name'] 

        self.redirect(webapp2.uri_for('user', username=username))

        # don't allow users to follow themselves
        if thisuser == username:
            return

        if 'unfollow' in self.request.GET:
            op = 'del'
            unop = 'add'
        else:
            op = 'add'
            unop = 'del'
  
        xg_on = db.create_transaction_options(xg=True)

        def txn(thisuser, area, op):
            tu, oa = db.get([thisuser, area])

            if not tu:
                tu = models.AreasFollowingIndex(key=thisuser)
            if not ou:
                oa = models.AreasFollowersIndex(key=area)

            changed = []
            if op == 'add':
                if thisuser.name() not in ou.users:
                    ou.users.append(thisuser.name())
                    changed.append(ou)
                if otheruser.name() not in tu.users:
                    tu.users.append(otheruser.name())
                    changed.append(tu)
            elif op == 'del':
                if thisuser.name() in ou.users:
                    ou.users.remove(thisuser.name())
                    changed.append(ou)
                if otheruser.name() in tu.users:
                    tu.users.remove(otheruser.name())
                    changed.append(tu)

            db.put(changed)

            return tu, ou

        followers_key = db.Key.from_path('User', username, 'UserFollowersIndex', username)
        following_key = db.Key.from_path('User', thisuser, 'UserFollowingIndex', thisuser)

        following, followers = db.run_in_transaction_options(xg_on, txn, following_key, followers_key, op)

        if op == 'add':
            self.add_message('success', 'You are now following %s.' %username)
            models.Activity.create(cache.get_by_key(self.session['user']['key']), models.ACTIVITY_FOLLOWING, user)
        elif op == 'del':
            self.add_message('success', 'You are no longer following %s.' %username)

        cache.set_multi({
            cache.C_FOLLOWERS %username: followers.users,
            cache.C_FOLLOWING %thisuser: following.users,
        })

class FollowAreaHandler(BaseHandler):
    def get(self, username, area_name):
        area = cache.get_area(None, area_name)
        if not area :
            self.error(404)
            return 
        thisuser = username  #self.session['user']['name']
   
        if 'user' not in self.session:
            logging.error("FollowAreaHandler() Error: user %s not logged in", username)
            self.error(404)
            return
            
        if username != self.session['user']['name']:
            logging.error ("FollowAreaHandler() Error: different user logged in %s %s", username, self.session['user']['name'])
            self.error(404)
            return

        if 'unfollow' in self.request.GET:
            op = 'del'
            unop = 'add'
            #print 'unfollowing'
        else:
            op = 'add'
            unop = 'del'
            #print 'following'

        xg_on = db.create_transaction_options(xg=True)

        def txn(userfollowingareas_key, areafollowers_key, area, op):
            tu, ar = db.get([userfollowingareas_key, areafollowers_key])
            #print("FollowAreaHandler() adding key=", userfollowingareas_key)
            if not tu:
                logging.error("FollowAreaHandler() user not found: %s", userfollowingareas_key)
                tu = models.UserFollowingAreasIndex(key=userfollowingareas_key)
            if not ar:
                logging.error("FollowAreaHandler() area not found: %s", areafollowers_key)
                ar = models.AreaFollowersIndex(key=areafollowers_key)  # FIXME: This looks wrong, ar is initialised as an area above but an afi here. Probably never executes.
    
            changed = []
            if not area.followers_count:
                area.followers_count = 0
                
            if op == 'add':
                if userfollowingareas_key.name() not in ar.users:
                    ar.users.append(userfollowingareas_key.name())
                    changed.append(ar)
                if areafollowers_key.name() not in tu.areas:
                    tu.areas.append(areafollowers_key.name())
                    changed.append(tu)
                    area.followers_count += 1
                    changed.append(area)
            elif op == 'del':
                if userfollowingareas_key.name() in ar.users:
                    ar.users.remove(userfollowingareas_key.name())
                    changed.append(ar)
                if areafollowers_key.name() in tu.areas:
                    area.followers_count -= 1
                    changed.append(area)
                    tu.areas.remove(areafollowers_key.name())
                    changed.append(tu)

            db.put(changed)

            return tu, ar
        
        following_key = db.Key.from_path('User', thisuser, 'UserFollowingAreasIndex', thisuser)
        followers_key = db.Key.from_path('AreaOfInterest', area_name.decode('utf-8'), 'AreaFollowersIndex', area_name.decode('utf-8'))

        areas_following, followers,  = db.run_in_transaction_options(xg_on, txn, following_key, followers_key, area, op)

        if op == 'add':
            self.add_message('success', 'You are now following area <em>%s</em>.' %area_name.decode('utf-8'))
            models.Activity.create(cache.get_by_key(self.session['user']['key']), models.ACTIVITY_FOLLOWING, area)

            ########### create a journal for each followed area - should be in above txn and a function call as duplicated ##############
            name = "Observations for " + area_name.decode('utf-8') # name is used by view-obstask.html to make reports.
            journal = models.Journal(parent=db.Key(self.session['user']['key']), name=name)
            for journal_url, journal_name, journal_type in self.session['journals']:
                if journal.name == journal_name:
                    self.add_message('info', 'You already have a journal called <em>%s</em>.' %name.decode('utf-8'))
                    break
            else:
                journal.journal_type = "observations"
                def txn2(user_key, journal):
                    user = db.get(user_key)
                    user.journal_count += 1
                    db.put([user, journal])
                    return user, journal

                user, journal = db.run_in_transaction(txn2, self.session['user']['key'], journal)
                cache.clear_journal_cache(db.Key(self.session['user']['key']))
                models.Activity.create(user, models.ACTIVITY_NEW_JOURNAL, journal.key())
                cache.set(cache.pack(user), cache.C_KEY, user.key())
                self.add_message('success', 'Created journal <em>%s</em>.' %name.decode('utf-8'))

        elif op == 'del':
            self.add_message('success', 'You are no longer following area <em>%s</em>.' %area_name.decode('utf-8'))
            
        cache.flush() # FIXME: Better fix by setting data into the cache as this will be expensive!!!
        #cache.set_multi({
        #    cache.C_AREA_FOLLOWERS %area.name: followers.users,  #doesn't look right.
        #    cache.C_FOLLOWING_AREAS %thisuser: areas_following #areas_following.areas,
        #})
        # For newJournal cache.set(cache.pack(user), cache.C_KEY, user.key())
        # cache.C_FOLLOWERS %username: followers.users,
        # cache.C_FOLLOWING %thisuser: following.users,

        cache.clear_area_cache(self.session['user']['key'], area.key() )
        #cache.clear_area_followers(area.key())
    
        #counters.increment(counters.COUNTER_AREAS) # should be FOLLOW_AREAS

        self.populate_user_session()
        if op == 'add':
            self.redirect(webapp2.uri_for('view-area', area_name=area.name))
        else:
            self.redirect(webapp2.uri_for('main'))
        return


class NewEntryHandler(BaseHandler):
    def get(self, username, journal_name, images=""):
        #print ("NewEntryHandler: ", journal_name, images)
        if username != self.session['user']['name']:
            logging.error("NewEntryHandler missing user", username, journal_name, images)
            self.error(404)
            return
        journal_key = cache.get_journal_key(username, journal_name)
        if not journal_key:
            logging.error("NewEntryHandler missing journal_key")
            self.error(404)
            return

        def txn(user_key, journal_key, entry, content):
            user, journal = db.get([user_key, journal_key])
            journal.entry_count += 1
            user.entry_count += 1

            db.put([user, journal, entry, content])
            return user, journal
        
        handmade_key = db.Key.from_path('Entry', 1, parent=journal_key)
        entry_id = db.allocate_ids(handmade_key, 1)[0]
        entry_key = db.Key.from_path('Entry', entry_id, parent=journal_key)

        handmade_key = db.Key.from_path('EntryContent', 1, parent=entry_key)
        content_id = db.allocate_ids(handmade_key, 1)[0]
        content_key = db.Key.from_path('EntryContent', content_id, parent=entry_key)

        content = models.EntryContent(key=content_key)
        entry = models.Entry(key=entry_key, content=content_id)
        
        if images:
            #content.images= [i.strip() for i in self.request.get('images').split(',')]
            content.images= [i.strip() for i in images.split(',')]
        else:
            images= []
                
        user, journal = db.run_in_transaction(txn, self.session['user']['key'], journal_key, entry, content)

        # move this to new entry saving for first time
        models.Activity.create(user, models.ACTIVITY_NEW_ENTRY, entry.key())

        counters.increment(counters.COUNTER_ENTRIES)
        cache.clear_entries_cache(journal.key())
        cache.set_keys([user, journal, entry, content])
        cache.set(cache.pack(journal), cache.C_JOURNAL, username, journal_name)

        if user.facebook_token and user.facebook_enable:
            taskqueue.add(queue_name='retry-limit', url=webapp2.uri_for('social-post'), params={'entry_key': entry_key, 'network': models.USER_SOURCE_FACEBOOK, 'username': user.name})
        if user.twitter_key and user.twitter_enable:
            taskqueue.add(queue_name='retry-limit', url=webapp2.uri_for('social-post'), params={'entry_key': entry_key, 'network': models.USER_SOURCE_TWITTER, 'username': user.name})

        self.redirect(webapp2.uri_for('view-entry', username=username, journal_name=journal_name, entry_id=entry_id))

class ViewEntryHandler(BaseHandler):
    def get(self, username, journal_name, entry_id):
        journal_name = journal_name.decode('utf-8')

        if self.session['user']['name'] != username:
            self.error(404) # should probably be change to 401 or 403
            return
        
        journal= cache.get_journal(username, journal_name)

        #logging.info('ViewEntryHandler journal_name %s %s', journal_name, journal)
        entry, content, blobs = cache.get_entry(username, journal_name, entry_id)
        if not entry:
            self.error(404)
            return

        user = cache.get_user(username)

        if 'pdf' in self.request.GET:
            pdf_blob = models.Blob.get_by_key_name('pdf', parent=entry)
            error = None

            # either no cached entry, or it's outdated
            if not pdf_blob or pdf_blob.date < entry.last_edited:
                if pdf_blob:
                    pdf_blob.blob.delete()

                file_name = files.blobstore.create(mime_type='application/pdf')
                subject = content.subject if content.subject else filters.jdate(entry.date)
                with files.open(file_name, 'a') as f:
                    error = utils.convert_html(f, subject, [(entry, content, blobs)])
                files.finalize(file_name)
                pdf_blob = models.Blob(
                    key_name='pdf',
                    parent=entry,
                    blob=files.blobstore.get_blob_key(file_name),
                    type=models.BLOB_TYPE_PDF,
                    name='%s - %s - %s' %(username, utils.deunicode(journal_name), subject),
                    date=entry.last_edited,
                )

                if error:
                    pdf_blob.blob.delete()
                    self.add_message('danger', 'Error while converting to PDF: %s' %error)
                else:
                    pdf_blob.put()

            if not error:
                self.redirect(pdf_blob.get_url(name=True))
                return
        if not journal:
            type = "default"
        else:
            type = journal.journal_type
            
        self.render('entry.html', {
            'blobs': blobs,
            'content': content,
            'entry': entry,
            'show_navbar': True,
            'journal_type': type,
            'journal_name': journal_name,
            'render': cache.get_entry_render(username, journal_name, entry_id),
            'username': username,
            'upload_url': webapp2.uri_for('upload-url', username=username, journal_name=journal_name, entry_id=entry_id),
            'can_upload': user.can_upload(),
            'markup_options': utils.render_options(models.CONTENT_TYPE_CHOICES, content.markup),
        })

class GetUploadURL(BaseHandler):
    def get(self, username, journal_name, entry_id):
        user = cache.get_by_key(self.session['user']['key'])
        if user.can_upload() and user.name == username:
            self.response.out.write(blobstore.create_upload_url(
                webapp2.uri_for('upload-file',
                    username=username,
                    journal_name=journal_name,
                    entry_id=entry_id
                ),
                max_bytes_per_blob=models.Blob.MAXSIZE
            ))
        else:
            self.response.out.write('')

class SaveEntryHandler(BaseHandler):
    def post(self):
        username = self.request.get('username')
        journal_name = self.request.get('journal_name')
        entry_id = long(self.request.get('entry_id'))
        delete = self.request.get('delete')

        if username != self.session['user']['name']:
            self.error(404)
            return

        self.redirect(webapp2.uri_for('view-entry', username=username, journal_name=journal_name, entry_id=entry_id))

        entry, content, blobs = cache.get_entry(username, journal_name, entry_id)

        if delete == 'delete':
            journal_key = entry.key().parent()
            user_key = journal_key.parent()

            def txn(user_key, journal_key, entry_key, content_key, blobs):
                entry = db.get(entry_key)
                delete = [entry_key, content_key]
                delete.extend([i.key() for i in blobs])
                db.delete_async(delete)

                user, journal = db.get([user_key, journal_key])
                journal.entry_count -= 1
                user.entry_count -= 1

                journal.chars -= entry.chars
                journal.words -= entry.words
                journal.sentences -= entry.sentences

                user.chars -= entry.chars
                user.words -= entry.words
                user.sentences -= entry.sentences

                for i in blobs:
                    user.used_data -= i.size

                user.count()
                db.put_async(user)

                # just deleted the last journal entry
                if journal.entry_count == 0:
                    journal.last_entry = None
                    journal.first_entry = None

                # only 1 left (but there are 2 in the datastore still)
                else:
                    # find last entry
                    entries = models.Entry.all().ancestor(journal).order('-date').fetch(2)
                    logging.info('%s last entries returned', len(entries))
                    for e in entries:
                        if e.key() != entry.key():
                            journal.last_entry = e.date
                            break
                    else:
                        logging.error('Did not find n last entry not %s', entry.key())

                    # find first entry
                    entries = models.Entry.all().ancestor(journal).order('date').fetch(2)
                    logging.info('%s first entries returned', len(entries))
                    for e in entries:
                        if e.key() != entry.key():
                            journal.first_entry = e.date
                            break
                    else:
                        logging.error('Did not find n first entry not %s', entry.key())

                journal.count()
                db.put(journal)
                return user, journal

            user, journal = db.run_in_transaction(txn, user_key, journal_key, entry.key(), content.key(), blobs)

            blobstore.delete([i.get_key('blob') for i in blobs])

            db.delete([entry, content])
            counters.increment(counters.COUNTER_ENTRIES, -1)
            counters.increment(counters.COUNTER_CHARS, -entry.chars)
            counters.increment(counters.COUNTER_SENTENCES, -entry.sentences)
            counters.increment(counters.COUNTER_WORDS, -entry.words)
            cache.clear_entries_cache(journal_key)
            cache.set_keys([user, journal])
            cache.set(cache.pack(journal), cache.C_JOURNAL, username, journal_name)
            self.add_message('success', 'Entry deleted.')
            self.redirect(webapp2.uri_for('view-journal', username=username, journal_name=journal_name))

        else:
            subject = self.request.get('subject').strip()
            tags = self.request.get('tags').strip()
            images = self.request.get('images').strip()
            text = self.request.get('text').strip()
            markup = self.request.get('markup')
            blob_list = self.request.get_all('blob')

            date = self.request.get('date').strip()
            time = self.request.get('time').strip()
            if not date:
                newdate = entry.date
            else:
                if not time:
                    time = '00:00'
                try:
                    #Bootstrap3's date-control format 2014-12-31 23:59 
                    newdate = datetime.datetime.strptime('{0!s} {1!s}'.format(date, time),'%Y-%m-%d %H:%M')
                except:
                    self.add_message('danger', 'Couldn\'t understand that date: {0!s} {1!s}'.format(date, time))
                    newdate = entry.date

            if tags:
                tags = [i.strip() for i in self.request.get('tags').split(',')]
            else:
                tags = []
            if images:
                images= [i.strip() for i in self.request.get('images').split(',')]
            else:
                images= []

            def txn(entry_key, content_key, rm_blobs, subject, tags, images, text, markup, rendered, chars, words, sentences, date):
                db.delete_async(rm_blobs)

                user, journal, entry  = db.get([entry_key.parent().parent(), entry_key.parent(), entry_key])

                dchars = -entry.chars + chars
                dwords = -entry.words + words
                dsentences = -entry.sentences + sentences

                journal.chars += dchars
                journal.words += dwords
                journal.sentences += dsentences

                #user.chars += dchars
                #user.words += dwords
                #user.sentences += dsentences

                entry.chars = chars
                entry.words = words
                entry.sentences = sentences

                entry.date = date

                user.set_dates()
                user.count()

                content = models.EntryContent(key=content_key)
                content.subject = subject
                content.tags = tags
                content.images = images
                content.text = text
                content.markup = markup
                content.rendered = rendered

                for i in rm_blobs:
                    user.used_data -= i.size
                    entry.blobs.remove(str(i.key().id()))

                db.put_async([user, entry, content])

                # just added the first journal entry
                if journal.entry_count == 1:
                    journal.last_entry = date
                    journal.first_entry = date
                else:
                    # find last entry
                    entries = models.Entry.all().ancestor(journal).order('-date').fetch(2)
                    logging.info('%s last entries returned', len(entries))
                    for e in entries:
                        if e.key() != entry.key():
                            if date > e.date:
                                journal.last_entry = date
                            else:
                                journal.last_entry = e.date
                            break
                    else:
                        logging.error('Did not find n last entry not %s', entry.key())

                    # find first entry
                    entries = models.Entry.all().ancestor(journal).order('date').fetch(2)
                    logging.info('%s first entries returned', len(entries))
                    for e in entries:
                        if e.key() != entry.key():
                            if date < e.date:
                                journal.first_entry = date
                            else:
                                journal.first_entry = e.date
                            break
                    else:
                        logging.error('Did not find n first entry not %s', entry.key())

                journal.count()
                db.put(journal)
                return user, journal, entry, content, dchars, dwords, dsentences

            rm_blobs = []

            for b in blobs:
                bid = str(b.key().id())
                if bid not in blob_list:
                    b.delete()
                    rm_blobs.append(b)

            for b in rm_blobs:
                blobs.remove(b)

            rendered = utils.markup(text, markup)

            if text:
                nohtml = html.strip_tags(rendered)
                chars = len(nohtml)
                words = len(entry.WORD_RE.findall(nohtml))
                sentences = len(entry.SENTENCE_RE.split(nohtml))
            else:
                chars = 0
                words = 0
                sentences = 0

            user, journal, entry, content, dchars, dwords, dsentences = db.run_in_transaction(txn, entry.key(), content.key(), rm_blobs, subject, tags, images, text, markup, rendered, chars, words, sentences, newdate)
            models.Activity.create(cache.get_user(username), models.ACTIVITY_SAVE_ENTRY, entry.key())

            counters.increment(counters.COUNTER_CHARS, dchars)
            counters.increment(counters.COUNTER_SENTENCES, dsentences)
            counters.increment(counters.COUNTER_WORDS, dwords)

            entry_render = utils.render('entry-render.html', {
                'blobs': blobs,
                'content': content,
                'entry': entry,
                'show_navbar': True,
                'entry_url': webapp2.uri_for('view-entry', username=username, journal_name=journal_name, entry_id=entry_id),
            })
            cache.set(entry_render, cache.C_ENTRY_RENDER, username, journal_name, entry_id)
            cache.set_keys([user, journal])
            cache.set_multi({
                cache.C_KEY %user.key(): cache.pack(user),
                cache.C_ENTRY_RENDER %(username, journal_name, entry_id): entry_render,
                cache.C_ENTRY %(username, journal_name, entry_id): (cache.pack(entry), cache.pack(content), cache.pack(blobs)),
            })

            #if user.dropbox_enable and user.dropbox_token:
            #    taskqueue.add(queue_name='retry-limit', url=webapp2.uri_for('backup'), params={'entry_key': entry.key(), 'network': models.USER_BACKUP_DROPBOX, 'journal_name': journal_name, 'username': username})
            #if user.google_docs_enable and user.google_docs_token:
            #    taskqueue.add(queue_name='retry-limit', url=webapp2.uri_for('backup'), params={'entry_key': entry.key(), 'network': models.USER_BACKUP_GOOGLE_DOCS, 'journal_name': journal_name, 'username': username})

            self.add_message('success', 'Your entry has been saved.')

        cache.clear_entries_cache(entry.key().parent())
        cache.set((cache.pack(entry), cache.pack(content), cache.pack(blobs)), cache.C_ENTRY, username, journal_name, entry_id)

class UploadHandler(BaseUploadHandler):
    def post(self, username, journal_name, entry_id):
        if username != self.session['user']['name']:
            self.error(404)
            return

        entry_key = cache.get_entry_key(username, journal_name, entry_id)
        uploads = self.get_uploads()

        blob_type = -1
        if len(uploads) == 1:
            blob = uploads[0]
            if blob.content_type.startswith('image/'):
                blob_type = models.BLOB_TYPE_IMAGE

        if not entry_key or self.session['user']['name'] != username or blob_type == -1:
            for upload in uploads:
                upload.delete()
            return

        def txn(user_key, entry_key, blob):
            user, entry = db.get([user_key, entry_key])
            user.used_data += blob.size
            entry.blobs.append(str(blob.key().id()))
            db.put([user, entry, blob])
            return user, entry

        handmade_key = db.Key.from_path('Blob', 1, parent=entry_key)
        blob_id = db.allocate_ids(handmade_key, 1)[0]

        blob_key = db.Key.from_path('Blob', blob_id, parent=entry_key)
        new_blob = models.Blob(key=blob_key, blob=blob, type=blob_type, name=blob.filename, size=blob.size)
        new_blob.get_url()

        user, entry = db.run_in_transaction(txn, entry_key.parent().parent(), entry_key, new_blob)
        cache.delete([
            cache.C_KEY %user.key(),
            cache.C_KEY %entry.key(),
            cache.C_ENTRY %(username, journal_name, entry_id),
            cache.C_ENTRY_RENDER %(username, journal_name, entry_id),
        ])
        cache.clear_entries_cache(entry.key().parent())

        self.redirect(webapp2.uri_for('upload-success', blob_id=blob_id, name=new_blob.name, size=new_blob.size, url=new_blob.get_url()))

class UploadSuccess(BaseHandler):
    def get(self):
        d = dict([(i, self.request.get(i)) for i in [
            'blob_id',
            'name',
            'size',
            'url',
        ]])

        self.response.out.write(json.dumps(d))

class FlushMemcache(BaseHandler): #Admin Only Function
    def get(self):
        cache.flush()
        self.render('admin.html', {
                                   'msg': ' memcache flushed',
                                      'show_navbar': True})

class NewBlogHandler(BaseHandler):  #Admin Only Function
    def get(self):
        b = models.BlogEntry(user=self.session['user']['name'], avatar=self.session['user']['avatar'])
        b.put()
        self.redirect(webapp2.uri_for('edit-blog', blog_id=b.key().id()))

class EditBlogHandler(BaseHandler):  #Admin Only Function
    def get(self, blog_id):
        b = models.BlogEntry.get_by_id(long(blog_id))

        if not b:
            self.error(404)
            return

        self.render('edit-blog.html', {
            'b': b,
            'markup_options': utils.render_options(models.RENDER_TYPE_CHOICES, b.markup),
        })

    def post(self, blog_id):
        b = models.BlogEntry.get_by_id(long(blog_id))
        delete = self.request.get('delete')

        if not b:
            self.error(404)
            return

        if delete == 'Delete entry':
            b.delete()

            if not b.draft:
                def txn():
                    c = models.Config.get_by_key_name('blog_count')
                    c.count -= 1
                    c.put()

                db.run_in_transaction(txn)

            cache.clear_blog_entries_cache()
            self.add_message('success', 'Blog entry deleted.')
            self.redirect(webapp2.uri_for('blog-drafts'))
            return

        title = self.request.get('title').strip()
        if not title:
            self.add_message('danger', 'Must specify a title.')
        else:
            b.title = title

        b.text = self.request.get('text').strip()
        b.markup = self.request.get('markup')
        b.slug = '%s-%s' %(blog_id, utils.slugify(b.title))

        draft = self.request.get('draft') == 'on'

        # new post
        if not draft and b.draft:
            blog_count = 1
        # was post, now draft
        elif draft and not b.draft:
            blog_count = -1
        else:
            blog_count = 0

        if blog_count:
            def txn(config_key, blog_count):
                c = db.get(config_key)
                c.count += blog_count
                c.put()

            c = models.Config.get_or_insert('blog_count', count=0)
            db.run_in_transaction(txn, c.key(), blog_count)
            cache.clear_blog_entries_cache()

        b.draft = draft

        date = self.request.get('date').strip()
        time = self.request.get('time').strip()

        try:
            b.date = datetime.datetime.strptime('%s %s' %(date, time), '%m/%d/%Y %I:%M %p')
        except:
            self.add_message('danger', 'Couldn\'t understand that date: %s %s' %(date, time))

        b.rendered = utils.markup(b.text, b.markup)

        b.put()
        self.add_message('success', 'Blog entry saved.')
        self.redirect(webapp2.uri_for('edit-blog', blog_id=blog_id))

class BlogHandler(BaseHandler):
    def get(self):
        page = int(self.request.get('page', 1))
        entries = cache.get_blog_entries_page(page)
        pages = cache.get_blog_count() / models.BlogEntry.ENTRIES_PER_PAGE
        if pages < 1:
            pages = 1

        if page < 1 or page > pages:
            self.error(404)
            return

        self.render('blog.html', {
            'entries': entries,
            'page': page,
            'pages': pages,
            'pagelist': utils.page_list(page, pages),
            'top': cache.get_blog_top(),
        })

class BlogEntryHandler(BaseHandler):
    def get(self, entry):
        blog_id = long(entry.partition('-')[0])
        entry = models.BlogEntry.get_by_id(blog_id)

        self.render('blog-entry.html', {
            'entry': entry,
            'top': cache.get_blog_top(),
        })

class BlogDraftsHandler(BaseHandler):
    def get(self):
        entries = models.BlogEntry.all().filter('draft', True).order('-date').fetch(500)
        self.render('blog-drafts.html', {
            'entries': entries,
        })

class MarkupHandler(BaseHandler):
    def get(self):
        self.render('markup.html')

#class SecurityHandler(BaseHandler):
#    def get(self):
#        self.render('security.html')

class UpdateUsersHandler(BaseHandler): #Admin Only Function
    def get(self):
        q = models.User.all(keys_only=True)
        cursor = self.request.get('cursor')

        if cursor:
            q.with_cursor(cursor)

        def txn(user_key):
            u = db.get(user_key)

            # custom update code here

            u.put()
            return u

        LIMIT = 10
        ukeys = q.fetch(LIMIT)
        for u in ukeys:
            user = db.run_in_transaction(txn, u)
            self.response.out.write('<br>updated %s: %s' %(user.name, user.lname))

        if len(ukeys) == LIMIT:
            self.response.out.write('<br><a href="%s">next</a>' %webapp2.uri_for('update-users', cursor=q.cursor()))
        else:
            self.response.out.write('<br>done')

class BlobHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, key):
        blob_info = blobstore.BlobInfo.get(key)

        name = self.request.get('name')
        if name == 'True':
            name = True

        if not blob_info:
            self.error(404)
        else:
            self.send_blob(blob_info, save_as=name)

# from https://github.com/ryanwi/twitteroauth/blob/master/source/main.py
class TwitterHandler(BaseHandler):
    def get(self, action):
        if 'user' not in self.session:
            self.redirect(webapp2.uri_for('main'))
            return

        self._client = twitter.oauth_client(self)

        if action == 'login':
            self.login()
        elif action == 'callback':
            self.callback()

    def login(self):
        # get a request token
        raw_request_token = self._client.get_request_token()

        self.session['twitter_token'] = raw_request_token.key
        self.session['twitter_secret'] = raw_request_token.secret

        # get the authorize url and redirect to twitter
        authorize_url = self._client.get_authorize_url(raw_request_token)
        self.redirect(authorize_url)

    def callback(self):
        if 'denied' in self.request.GET:
            self.redirect(webapp2.uri_for('account'))

        # lookup request token
        raw_oauth_token = self.request.get('oauth_token')

        # get an access token for the authorized user
        oauth_token = twitter.oauth_token(self.session['twitter_token'], self.session['twitter_secret'])
        raw_access_token = self._client.get_access_token(oauth_token)

        # get the screen_name
        self._client = twitter.oauth_client(self, raw_access_token)
        screen_name = self._client.get('/account/verify_credentials')['screen_name']

        # store access token
        def txn(user_key, screen_name, key, secret):
            u = db.get(user_key)
            u.twitter_id = screen_name
            u.twitter_key = key
            u.twitter_secret = secret
            u.twitter_enable = True
            u.put()
            return u

        user = db.run_in_transaction(txn, self.session['user']['key'], screen_name, raw_access_token.key, raw_access_token.secret)
        cache.set_keys([user])
        self.redirect(webapp2.uri_for('account'))

class SocialPost(BaseHandler):
    def post(self):
        entry_key = db.Key(self.request.get('entry_key'))
        network = self.request.get('network')
        username = self.request.get('username')

        MESSAGE = 'Wrote a new entry on Bunjil.'
        NAME = 'my observations'
        link = utils.absolute_uri('user', username=username)

        user = cache.get_by_key(entry_key.parent().parent())

        if network == models.USER_SOURCE_FACEBOOK and all([user.facebook_token, user.facebook_enable]):
            data = facebook.graph_request(user.facebook_token, method='POST', path='/feed', payload_dict={
                'message': MESSAGE,
                'link': link,
                'name': NAME,
            })
        if network == models.USER_SOURCE_TWITTER and all([user.twitter_id, user.twitter_key, user.twitter_secret]):
            oauth_token = twitter.oauth_token(user.twitter_key, user.twitter_secret)
            client = twitter.oauth_client(None, oauth_token)
            status = client.post('/statuses/update', status='%s %s' %(MESSAGE, link))

class FollowingHandler(BaseHandler):
    def get(self, username):
        u = cache.get_user(username)
        following = cache.get_by_keys(cache.get_following(username), 'User')
        followers = cache.get_by_keys(cache.get_followers(username), 'User')

        self.render('following.html', {'u': u, 'following': following, 'followers': followers})

class DownloadJournalHandler(BaseHandler):
    def get(self, username, journal_name):
        if username != self.session['user']['name']:
            self.error(404)
            return

        journal_key = cache.get_journal_key(username, journal_name)

        if not journal_key:
            self.error(404)
            return

        journal = cache.get_by_key(journal_key)

        DATE_FORMAT = '%m/%d/%Y'
        errors = []
        error = None
        try:
            from_date = datetime.datetime.strptime(self.request.get('from'), DATE_FORMAT)
        except ValueError:
            if 'from' in self.request.GET:
                errors.append('from')
            from_date = journal.first_entry

        try:
            to_date = datetime.datetime.strptime(self.request.get('to'), DATE_FORMAT)
        except ValueError:
            if 'to' in self.request.GET:
                errors.append('to')
            to_date = journal.last_entry

        if not errors and 'format' in self.request.GET and from_date and to_date:
            key_name = 'pdf-%s-%s' %(from_date, to_date)
            key = db.Key.from_path('Blob', key_name, parent=journal_key)
            pdf_blob = db.get(key)

            # either no cached entry, or it's outdated
            if not pdf_blob or pdf_blob.date < journal.last_modified:
                if pdf_blob:
                    pdf_blob.blob.delete()

                file_name = files.blobstore.create(mime_type='application/pdf')
                title = '%s: %s to %s' %(journal.name, from_date.strftime(DATE_FORMAT), to_date.strftime(DATE_FORMAT))

                entries = []
                for entry_key in models.Entry.all(keys_only=True).ancestor(journal).filter('date >=', from_date).filter('date <', to_date + datetime.timedelta(1)).order('date'):
                    entries.append(cache.get_entry(username, journal_name, entry_key.id(), entry_key))

                with files.open(file_name, 'a') as f:
                    error = utils.convert_html(f, title, entries)
                files.finalize(file_name)
                pdf_blob = models.Blob(
                    key=key,
                    blob=files.blobstore.get_blob_key(file_name),
                    type=models.BLOB_TYPE_PDF,
                    name='%s - %s - %s to %s' %(username, utils.deunicode(journal_name.decode('utf-8')), from_date.strftime(DATE_FORMAT), to_date.strftime(DATE_FORMAT)),
                    date=journal.last_modified,
                )

                if error:
                    pdf_blob.blob.delete()
                    self.add_message('danger', 'Error while converting to PDF: %s' %error)
                else:
                    pdf_blob.put()

            if not error:
                self.redirect(pdf_blob.get_url(name=True))
                return

        self.render('download-journal.html', {
            'journal': journal,
            'username': username,
            'errors': errors,
            'from': self.request.get('from', from_date.strftime(DATE_FORMAT)),
            'to': self.request.get('to', to_date.strftime(DATE_FORMAT)),
        })

class DropboxCallback(BaseHandler):
    def get(self):
        if 'user' not in self.session:
            return

        if self.request.get('action') == 'authorize':
            token, url = utils.dropbox_url()
            self.session['dropbox_token'] = token
            self.redirect(url)
            return

        if 'dropbox_token' not in self.session:
            return

        def txn(user_key, dropbox_token, dropbox_uid):
            u = db.get(user_key)
            u.dropbox_token = dropbox_token
            u.dropbox_id = dropbox_uid
            u.dropbox_enable = True
            u.put()
            return u

        try:
            access_token = utils.dropbox_token(self.session['dropbox_token'])
            u = db.run_in_transaction(txn, self.session['user']['key'], str(access_token), self.request.get('uid'))
            cache.set_keys([u])
            self.add_message('success', 'Dropbox authorized.')
        except Exception, e:
            self.add_message('danger', 'An error occurred with Dropbox. Try again.')
            logging.error('Dropbox error: %s', e)

        self.redirect(webapp2.uri_for('account'))

class BackupHandler(BaseHandler):
    def post(self):
        entry_key = db.Key(self.request.get('entry_key'))
        network = self.request.get('network')
        username = self.request.get('username')
        journal_name = self.request.get('journal_name')

        user = cache.get_user(username)
        entry, content, blobs = cache.get_entry(username, journal_name, entry_key.id(), entry_key)
        path = '%s/%s.html' %(journal_name.replace('/', '_'), entry_key.id())
        rendered = utils.render('pdf.html', {'entries': [(entry, content, [])]})
        rendered = rendered.encode('utf-8')

        if network == models.USER_BACKUP_DROPBOX:
            try:
                put = utils.dropbox_put(user.dropbox_token, path, rendered, entry.dropbox_rev)
            except: # maybe a parent_rev problem? try again without
                try:
                    put = utils.dropbox_put(user.dropbox_token, path, rendered) # no parent rev
                except Exception, e:
                    logging.error('Dropbox put error: %s', e)
                    return

            def txn(entry_key, rev):
                e = db.get(entry_key)
                e.dropbox_rev = rev
                e.put()
                return e

            entry = db.run_in_transaction(txn, entry_key, put['rev'])
        elif network == models.USER_BACKUP_GOOGLE_DOCS:
            try:
                doc_id = utils.google_upload(user.google_docs_token, utils.deunicode(path), rendered, entry.google_docs_id)

                if doc_id and doc_id != entry.google_docs_id:
                    def txn(entry_key, doc_id):
                        e = db.get(entry_key)
                        e.google_docs_id = doc_id
                        e.put()
                        return e

                    entry = db.run_in_transaction(txn, entry_key, doc_id)
            except Exception, e:
                logging.error('Google Docs upload error: %s', e)

class GoogleSiteVerification(BaseHandler):
    def get(self):
        self.response.out.write('google-site-verification: %s.html' %settings.GOOGLE_SITE_VERIFICATION)

class GoogleCallback(BaseHandler):
    def get(self):
        if 'user' not in self.session:
            return

        if self.request.get('action') == 'authorize':
            self.redirect(str(utils.google_url()))
            return

        if 'token' in self.request.GET:
            def txn(user_key, token):
                u = db.get(user_key)
                u.google_docs_token = token
                u.google_docs_enable = True
                u.put()
                return u

            try:
                session_token = utils.google_session_token(self.request.get('token'))
                user = db.run_in_transaction(txn, self.session['user']['key'], session_token.get_token_string())
                cache.set_keys([user])
                self.add_message('success', 'Google Docs authorized.')
            except Exception, e:
                self.add_message('danger', 'An error occurred with Google Docs. Try again.')
                logging.error('Google Docs error: %s', e)

            self.redirect(webapp2.uri_for('account'))

SECS_PER_WEEK = 60 * 60 * 24 * 7

config = {
    'webapp2_extras.sessions': {
        'secret_key': settings.COOKIE_KEY,
        'session_max_age': SECS_PER_WEEK,
        'cookie_args': {'max_age': SECS_PER_WEEK},
    },
}

app = webapp2.WSGIApplication([
    webapp2.Route(r'_ah/warmup', handler=EarthEngineWarmUpHandler, name='earth-engine'),
    webapp2.Route(r'/', handler=MainPage, name='main'),
    webapp2.Route(r'/about', handler=AboutHandler, name='about'),
    webapp2.Route(r'/account', handler=AccountHandler, name='account'),
    webapp2.Route(r'/activity', handler=ActivityHandler, name='activity'),
    webapp2.Route(r'/admin/blog/<blog_id>', handler=EditBlogHandler, name='edit-blog'),
 
    webapp2.Route(r'/admin/drafts', handler=BlogDraftsHandler, name='blog-drafts'),
    webapp2.Route(r'/admin/flush', handler=FlushMemcache, name='flush-memcache'),
    webapp2.Route(r'/admin/new/blog', handler=NewBlogHandler, name='new-blog'),
    webapp2.Route(r'/admin/update/users', handler=UpdateUsersHandler, name='update-users'),
    webapp2.Route(r'/admin/checknew', handler=CheckNewAllAreasHandler, name='check-new-all-images'),
    webapp2.Route(r'/admin/obs/list', handler=ViewObservationTasksHandler,  handler_method='ViewObservationTasksForAll', name='view-obstasks'),
    webapp2.Route(r'/admin/checknew/<area_name>', handler=CheckNewAreaHandler, name='check-new-area-images'),
    webapp2.Route(r'/blob/<key>', handler=BlobHandler, name='blob'),
    webapp2.Route(r'/blog', handler=BlogHandler, name='blog'),
    webapp2.Route(r'/blog/<entry>', handler=BlogEntryHandler, name='blog-entry'),
    webapp2.Route(r'/donate', handler=DonateHandler, name='donate'),
    webapp2.Route(r'/dropbox', handler=DropboxCallback, name='dropbox'),
    webapp2.Route(r'/facebook', handler=FacebookCallback, name='facebook'),
    webapp2.Route(r'/google', handler=GoogleCallback, name='google'),
    webapp2.Route(r'/feeds/<feed>', handler=FeedsHandler, name='feeds'),
    webapp2.Route(r'/following/<username>', handler=FollowingHandler, name='following'),

    webapp2.Route(r'/register', handler=Register, name='register'),
    webapp2.Route(r'/new/user', handler=NewUserHandler, name='new-user'),
    #webapp2.Route(r'/login/google/<protected_url>', handler=GoogleLogin, name='login-google'),
    webapp2.Route(r'/login/google', handler=GoogleLogin, name='login-google'),
    webapp2.Route(r'/login/facebook', handler=FacebookLogin, name='login-facebook'),
    webapp2.Route(r'/logout', handler=Logout, name='logout'),
    webapp2.Route(r'/logout/google', handler=GoogleSwitch, name='logout-google'),
    webapp2.Route(r'/markup', handler=MarkupHandler, name='markup'),
    
    #webapp2.Route(r'/api-docs', include('rest_framework.urls', namespace='rest_framework')),
    webapp2.Route(r'/new/journal', handler=NewJournal, name='new-journal'),
    webapp2.Route(r'/save', handler=SaveEntryHandler, name='entry-save'),
    webapp2.Route(r'/mailtest', handler=MailTestHandler, name='mail-test'),
    webapp2.Route(r'/selectcell/<area_name>/<celldata>', handler=SelectCellHandler, name='select-cell'),

    webapp2.Route(r'/twitter/<action>', handler=TwitterHandler, name='twitter'),
    webapp2.Route(r'/upload/file/<username>/<journal_name>/<entry_id>', handler=UploadHandler, name='upload-file'),
    webapp2.Route(r'/upload/success', handler=UploadSuccess, name='upload-success'),
    webapp2.Route(r'/upload/url/<username>/<journal_name>/<entry_id>', handler=GetUploadURL, name='upload-url'),

    # observation tasks see also admin/obs/list
    webapp2.Route(r'/obs/list', handler=ViewObservationTasksHandler,  handler_method='ViewObservationTasksForAll', name='view-obstasks'),
    webapp2.Route(r'/obs/<username>/<task_name>', handler=ObservationTaskHandler, name='view-obstask'),

    webapp2.Route(r'/obs/<username>/overlay/create/<obskey>/<role>/<algorithm>', handler=CreateOverlayHandler, name='create-overlay'), #AJAX call
    webapp2.Route(r'/obs/<username>/overlay/update/<ovlkey>/<algorithm>', handler=UpdateOverlayHandler, name='update-overlay'), #AJAX call
    
    # taskqueue
    webapp2.Route(r'/tasks/social_post', handler=SocialPost, name='social-post'),
    webapp2.Route(r'/tasks/backup', handler=BackupHandler, name='backup'),
    
    # google site verification
    webapp2.Route(r'/%s.html' %settings.GOOGLE_SITE_VERIFICATION, handler=GoogleSiteVerification),
    
    
    webapp2.Route(r'/myareas', handler=ViewAreas, name='view-areas'), # view my areas
    webapp2.Route(r'/<username>/myareas', handler=ViewAreas, name='view-areas'), # view someone else's areas.

    webapp2.Route(r'/new/area', handler=NewAreaHandler, name='new-area'),                             # create area
    webapp2.Route(r'/area/<area_name>/delete', handler=DeleteAreaHandler, name='delete-area'),  # delete area (owner or admin)
    webapp2.Route(r'/area/<area_name>/update/share/<shared>', handler=UpdatedAreaSharedHandler, name='share-area'),  # updatearea (owner only)
    webapp2.Route(r'/area/<area_name>/update/view', handler=UpdateAreaViewHandler, name='area-save-view'),
    #webapp2.Route(r'/area/<area_name>/update/view/<lat:\d+>/<lng:\d+>/<zoom:\d+>/', handler=UpdateAreaViewHandler, name='area-save-view'),
    
    webapp2.Route(r'/area/<area_name>/getcells', handler=GetLandsatCellsHandler, name='get-cells'), #ajax
    webapp2.Route(r'/area/<area_name>', handler=ViewArea, name='view-area'),  # view area.
    
    webapp2.Route(r'/<username>/follow/<area_name>', handler=FollowAreaHandler, name='follow-area'),   # start following area
    webapp2.Route(r'/<username>/unfollow/<area_name>', handler=FollowAreaHandler, name='follow-area'),# stop following area
    
    webapp2.Route(r'/area/<area_name>/action/<action>/<satelite>/<algorithm>/<latest>', handler=LandsatOverlayRequestHandler, name='new-landsat-overlay'),
    webapp2.Route(r'/area/<area_name>/action/<action>/<satelite>/<algorithm>/<latest>/<path:\d+>/<row:\d+>', handler=LandsatOverlayRequestHandler, name='new-landsat-overlay'),
   
         
    # this section must be last, since the regexes below will match one and two -level URLs
    webapp2.Route(r'/<username>/journals',  handler=ViewJournalsHandler, name='view-journals'),
    webapp2.Route(r'/<username>', handler=UserHandler, name='user'),
    webapp2.Route(r'/<username>/journal/<journal_name>', handler=ViewJournal, name='view-journal'),
    webapp2.Route(r'/<username>/journal/<journal_name>/<entry_id:\d+>', handler=ViewEntryHandler, name='view-entry'),
    webapp2.Route(r'/<username>/journal/<journal_name>/download', handler=DownloadJournalHandler, name='download-journal'),
    webapp2.Route(r'/<username>/journal/<journal_name>/new/<images:[^/]+>', handler=NewEntryHandler, name='new-entry'),
    webapp2.Route(r'/<username>/journal/<journal_name>/new', handler=NewEntryHandler, name='new-entry')
   #(decorator.callback_path, decorator.callback_handler())
    
    ], debug=True, config=config)

RESERVED_NAMES = set([
    '',
    '_ah',
    'warmup',
    '<username>',
    'about',
    'myareas',
    'account',
    'activity',
    'area',
    'areas',
    'admin',
    'backup',
    'blob',
    'blog',
    'checknew',
    'contact',
    'delete',
    'docs',
    'donate',
    'dropbox',
    'entry',
    'engine',
    'facebook',
    'features',
    'feeds',
    'file',
    'follow',
    'followers',
    'following',
    'google',
    'googledocs',
    'googleplus',
    'help',
    'image',
    'journal',
    'journals',
    'login',
    'logout',
    'list',
    'markup',
    'mailtest',
    'new',
    'news',
    'oauth',
    'obs',
    'obstasks'
    'openid',
    'prior',
    'privacy',
    'register',
    'save',
    #'security',
    'site',
    'selectcell',
    'stats',
    'observatory',
    'tasks',
    'terms',
    'twitter',
    'upload',
    'user',
    'users'
])


# assert that all routes are listed in RESERVED_NAMES
for i in app.router.build_routes.values():
    
    name = i.template.partition('/')[2].partition('/')[0]
    if name not in RESERVED_NAMES:
        import sys
        logging.critical('%s not in RESERVED_NAMES', name)
        logging.error('%s not in RESERVED_NAMES' %name)
        sys.exit(1)
        