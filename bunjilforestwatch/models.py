""" Modles.py intro
etc.....
"""

from __future__ import with_statement
import datetime
import logging
import re
from google.appengine.api import images
from google.appengine.ext import ndb
from google.appengine.api import app_identity

import webapp2

import eeservice
import ee
import hashlib
import geojson
import json


# from contrib.gis.geos import coords

class DerefModel(ndb.Model):
    def get_key(self, prop_name):
        # return getattr(self.__class__, prop_name).get_value_for_datastore(self)
        return getattr(self.__class__, prop_name)


class DerefExpando(ndb.Expando):
    def get_key(self, prop_name):
        return getattr(self.__class__, prop_name).get_value_for_datastore(self)


USER_SOURCE_FACEBOOK = 'facebook'
USER_SOURCE_GOOGLE = 'google'
USER_SOURCE_TWITTER = 'twitter'

USER_SOURCE_CHOICES = [
    USER_SOURCE_FACEBOOK,
    USER_SOURCE_GOOGLE,
]

USER_SOCIAL_NETWORKS = [
    USER_SOURCE_FACEBOOK,
    USER_SOURCE_TWITTER,
]


class User(ndb.Model):
    """ ndb.User builds on google user
    
    """

    name = ndb.StringProperty(required=True, indexed=False)
    lname = ndb.StringProperty(indexed=True)
    email = ndb.StringProperty()
    register_date = ndb.DateTimeProperty(auto_now_add=True)
    last_active = ndb.DateTimeProperty(auto_now=True)
    token = ndb.StringProperty(required=True, indexed=False)

    areas_observing = ndb.KeyProperty(repeated=True, default=None)
    """ list of areas we watch - Not Used
    """
    areas_subscribing = ndb.KeyProperty(repeated=True, default=None)  # list of areas I subscribe to  (only for local)

    role = ndb.StringProperty(required=True, choices=set(["volunteer", "local", "admin", "viewer"]))
    """roles for bunjil app users are either volunteer, local, admin or viewer. 
    """

    # not required
    first_entry = ndb.DateTimeProperty()
    last_entry = ndb.DateTimeProperty()
    entry_days = ndb.IntegerProperty(required=True, default=0)

    # these two properties will be deleted
    source = ndb.StringProperty(choices=USER_SOURCE_CHOICES)
    uid = ndb.StringProperty()

    google_id = ndb.StringProperty()
    allowed_data = ndb.IntegerProperty(required=True, default=50 * 2 ** 20)
    """ allowed_data has a 50 MB default
    """
    used_data = ndb.IntegerProperty(required=True, default=0)

    areas_count = ndb.IntegerProperty(required=True, default=0)

    journal_count = ndb.IntegerProperty(required=True, default=0)
    entry_count = ndb.IntegerProperty(required=True, default=0)

    facebook_id = ndb.StringProperty()
    facebook_enable = ndb.BooleanProperty(indexed=False)
    facebook_token = ndb.StringProperty(indexed=False)

    twitter_id = ndb.StringProperty()
    twitter_enable = ndb.BooleanProperty(indexed=False)
    twitter_key = ndb.StringProperty(indexed=False)
    twitter_secret = ndb.StringProperty(indexed=False)

    # not really required
    def count(self):
        if self.entry_count and self.last_entry and self.first_entry:
            self.entry_days = (self.last_entry - self.first_entry).days + 1
            weeks = self.entry_days / 7.
            self.freq_entries = self.entry_count / weeks
            # self.freq_chars = self.chars / weeks
            # self.freq_words = self.words / weeks
            # self.freq_sentences = self.sentences / weeks
        else:
            self.entry_days = 0
            self.freq_entries = 0.
            # self.freq_chars = 0.
            # self.freq_words = 0.
            # self.freq_sentences = 0.

    def set_dates(self):
        self.last_entry = datetime.datetime.now()

        if not self.first_entry:
            self.first_entry = self.last_entry

    def __str__(self):
        return str(self.name)

    def gravatar(self, size=''):
        if size:
            size = '&s=%s' % size

        if not self.email:
            email = ''
        else:
            email = self.email.lower()

        return '//www.gravatar.com/avatar/' + hashlib.md5(email).hexdigest() + '?d=mm%s' % size

    def can_upload(self):
        return self.bytes_remaining > 0

    @property
    def bytes_remaining(self):
        return self.allowed_data - self.used_data

    @property
    def sources(self):
        return [i for i in USER_SOURCE_CHOICES if getattr(self, '%s_id' % i)]

    def get_area_count(self):
        """
        @return: number of areas created by this user (integer)
        """
        return AreaOfInterest.query(AreaOfInterest.owner == self.key).count()


class UserFollowersIndex(ndb.Model):
    """ UserFollowersIndex - A User has a list of followers (other users) in an UserFollowersIndex(key=user).

    """
    users = ndb.StringProperty(repeated=True)


class UserFollowingIndex(ndb.Model):
    """UserFollowingIndex - A User has a list of other users they follow in a UserFollowingIndex(key=area)

    """
    users = ndb.StringProperty(repeated=True)


class AreaFollowersIndex(ndb.Model):
    """AreaFollowersIndex - An Area has a list of users in an AreaFollowersIndex(key=user)
    """
    users = ndb.StringProperty(repeated=True)
    count = ndb.ComputedProperty(lambda e: len(e.users))

    @staticmethod
    def get_key(area_name_decoded):
        return ndb.Key('AreaOfInterest', area_name_decoded, 'AreaFollowersIndex', area_name_decoded)


class UserFollowingAreasIndex(ndb.Model):
    """UserFollowingAreasIndex - A User has a list of areas in a UserFollowingAreasIndex(key=area). Adding a list of area keys.
    moving from list of string to list of keys
    """
    areas = ndb.StringProperty(repeated=True)
    area_keys = ndb.KeyProperty(kind='AreaOfInterest', repeated=True)

    @staticmethod
    def get_key(username):
        return ndb.Key('User', username, 'UserFollowingAreasIndex', username)

    @staticmethod  # returns a list of area names that the user follows.
    def get_by_username(username):
        return UserFollowingAreasIndex.get_key(username).get()


class AreaOfInterest(ndb.Model):
    """
    
    @cvar PUBLIC_AOI:
    @cvar UNLISTED_AOI: 
    @cvar PRIVATE_AOI: 
    @cvar ENTRIES_PER_PAGE: 
    @cvar MAX_AREAS:  
    @cvar MAX_OTHER_AREAS:  #Max Other Areas to show
    @todo Move ENTRIES_PER_PAGE and MAX_AREAS to settings.py

    @group Geometry:
        max_latlon, min_latlon, total_area, ft_docid, area_location, boundary_geojsonstr, boundary_hull, bounds 
    @group Users:
        created_by,    owner, share,

    @group Monitoring:
        cells, entry_count 

    @group Decription:
        name, description, description_why, description_who, description_how, threats, type, wiki

    @deprecated: coordinates
    """

    ENTRIES_PER_PAGE = 5 # TODO Move to Settings.py
    MAX_AREAS = 32       # TODO Move to Settings.py
    MAX_OTHER_AREAS = 48 # TODO Move to Settings.py

    PUBLIC_AOI = 0
    """Everyone can see and follow this area.share
    """

    UNLISTED_AOI = 1
    """Anyone with the link can see and follow this area.share
    """

    PRIVATE_AOI = 2
    """Only the owner can see or follow this area.share
    """

    """
    DESCRIPTIVE DATA
    """
    # Area Description
    name = ndb.StringProperty(required=True)
    # description = ndb.StringProperty(multiline=True) # text might be better type as it is not indexed.
    description = ndb.TextProperty()  # What? text type is longer but is not indexed.
    description_why = ndb.TextProperty()  # text type is longer but is not indexed.
    description_who = ndb.TextProperty()  # #who looks after this area?
    description_how = ndb.TextProperty()  # text type is longer but is not indexed.
    threats = ndb.TextProperty()  # text type is longer but is not indexed.
    type = ndb.StringProperty()
    wiki = ndb.StringProperty()  # beware max url 500 - like to a story about this area.

    """
    WDPA Attributes 
    """
    gov_type = ndb.StringProperty()  # 'Federal';'national ministry or agency', 'Sub-national ministry or agency', 'Government delegated management', 'Transboundary governance', 'Collaborative governance', 'Joint governance', 'Individual landowners', 'Non-profit organisations', 'For-profit organizations', 'Indigenous peoples', 'Community conserved areas' , 'Not Reported'.
    orig_name = ndb.StringProperty()  # name in original language
    desig_type = ndb.StringProperty()  # Allowed values: National, Regional, International.
    desig = ndb.StringProperty()  # Name of designation.
    IUCNcategory = ndb.StringProperty()  # Allowed values:Ia, Ib, II, III, IV, V, VI, Not Applicable, Not Applied, Not Reported. https://en.wikipedia.org/wiki/IUCN_protected_area_categories
    """ 
    Designation in English. 
    Allowed values for "international-level designations: Ramsar Site, Wetland of International Importance; UNESCO-MAB Biosphere Reserve ; World Heritage Site. 
    Allowed values for regional-level designations: Baltic Sea Protected Area (HELCOM) ; Cartagena Special Protected Area ; Marine Protected Area (CCAMLR) ; Marine Protected Area (OSPAR) ; Site of Community Importance (Habitats Directive) ; Special Protection Area (Habitats Directive) ; Specially Protected Area of Mediterranean Importance (Barcelona Convention). 
    No fixed values for protected areas designated at a national level
    """
    manag_auth = ndb.StringProperty()
    owner_type = ndb.StringProperty()  # State, Communal, Individual landowners, For-profit organizations, Non-profit organizations, Joint ownership, Multiple ownership, Contested, Not Reported.

    """
    GEOMETRY
    """
    max_latlon = ndb.GeoPtProperty(required=True, default=None)
    min_latlon = ndb.GeoPtProperty(required=True, default=None)
    total_area = ndb.FloatProperty(required=False, default=0)

    ft_docid = ndb.StringProperty()
    """A fusion table's document id. Only required if boundary provided in a FT.
    """
    area_location = ndb.GeoPtProperty(required=False, default=None)  # make this required one day.

    # coordinates = ndb.GeoPtProperty(repeated=True, default=None) # When a fusion table is provided in boundary_ft, this is the convexHull of the FT. #Deprecated

    boundary_geojsonstr = ndb.TextProperty(
        required=False)  # A geojson string encoded FC object who's geometry is the boundary provided by the owner. It may have a complex geometry

    boundary_hull = ndb.TextProperty(
        required=False)  # A geojson string encoded Feature object of the convex hull of the featureCollection or park boundary in JSON string format.

    bounds = ndb.FloatProperty(repeated=True, default=None)  # bounds of rectangle used for mapview.

    """ 
    VIEW - Parameters for viewing Area
    """
    map_center = ndb.GeoPtProperty(required=True, default=None)
    map_zoom = ndb.IntegerProperty(required=True, default=1)

    """
    MONITORING
    """
    cells = ndb.KeyProperty(repeated=True, default=None)
    """list of Landsat cells overlapping this area - calculated on new.
    """
    entry_count = ndb.IntegerProperty(required=True, default=0)
    """reports related to this area - not used yet
    """

    """
    USERS & SECURITY
    """
    # User (subscriber) who created AOI
    created_by = ndb.UserProperty(verbose_name=None, auto_current_user=False,
                                  auto_current_user_add=True)  # set automatically when created. never changes.
    # owner = ndb.ReferenceProperty(User) #key to subscriber that created area.   # set by caller. could be reassigned.
    owner = ndb.KeyProperty(kind=User)  # key to subscriber that created area.   # set by caller. could be reassigned.
    share = ndb.IntegerProperty(required=True, default=PUBLIC_AOI)  # set to hide area. see @properties below

    #followers_count = ndb.IntegerProperty(required=True, default=0)  # see num_followers()

    """
    TIMESTAMPS
    """
    created_date = ndb.DateTimeProperty(auto_now_add=True)
    last_modified = ndb.DateTimeProperty(auto_now=True)

    """
    PROPERTIES
    """

    def __unicode__(self):
        return unicode(self.name)

    @property
    def area_followers(self):
        return AreaFollowersIndex.get_key(self.name).get()

    @property
    def num_followers(self):
        followers = self.area_followers
        if followers:
            return followers.count
        return 0

    @property
    def pages(self):
        if self.entry_count == 0:
            return 1
        return (self.entry_count + self.ENTRIES_PER_PAGE - 1) / self.ENTRIES_PER_PAGE

    def url(self, page=1):
        if page > 1:
            # return webapp2.uri_for('view-area', username=self.key.parent().name(), area_name= self.name, page=page)
            return webapp2.uri_for('view-area', area_name=self.name, page=page)
        else:
            # return webapp2.uri_for('view-area', username=self.key.parent().name(),  area_name= self.name)
            return webapp2.uri_for('view-area', area_name=self.name)

    def tasks_url(self, page=1):
        if page > 1:
            # return webapp2.uri_for('view-area', username=self.key.parent().name(), area_name= self.name, page=page)
            # hostname = google.appengine.api.app_identity.app_identity.get_default_version_hostname()
            return webapp2.uri_for('view-obstasks', area_name=self.name, page=page)
        else:
            # return webapp2.uri_for('view-area', username=self.key.parent().name(),  area_name= self.name)
            return webapp2.uri_for('view-obstasks', area_name=self.name.encode('utf-8'))

    @property
    def area_location_geojson(self):
        """
        returns area_location (place mark provided by user) as a geoJSON Point Feature object
        """
        return {
            "type": "Feature",
            "properties": {
                "name": "area_location"
            },
            "geometry": {
                "type": "Point",
                "coordinates": [
                    self.area_location.lon, self.area_location.lat
                ]
            }

        }

    def set_area_location(self, feature):
        try:
            coordinates = feature['geometry']['coordinates']
        except KeyError:
            logging.error('KeyError in area_location Feature')
        self.area_location = ndb.GeoPt(
            float(coordinates[1]),
            float(coordinates[0]))
        '''
        area_location_geojson_feature = feature['geometry']
        area_location_coords = feature['geometry']['coordinates']
        geom= ee.Geometry(area_location_geojson_feature)
        feat = ee.Feature(geom, {'fill': 1})
        park_boundary_fc = ee.FeatureCollection(feat) # so far just a point
        area_location = ndb.GeoPt(float(area_location_geojson_feature['coordinates'][1]), float(area_location_geojson_feature['coordinates'][0]))
        '''

    @property
    def shared_str(self):
        if self.share == self.PUBLIC_AOI:
            return 'public'
        elif self.share == self.UNLISTED_AOI:
            return 'unlisted'
        elif self.share == self.PRIVATE_AOI:
            return 'private'
        else:
            return 'error'

    def set_shared(self, share_str):

        if self.share == self.PUBLIC_AOI and share_str == 'public':
            logging.error('BPA-138 Need to remove all followers except owner')
            #FIXME: Need to remove all followers except owner BPA-138

        if share_str == 'public':
            self.share = self.PUBLIC_AOI
            return self.share
        elif share_str == 'unlisted':
            self.share = self.UNLISTED_AOI
            return self.share
        elif share_str == 'private':
            self.share = self.PRIVATE_AOI
            return self.share
        else:
            logging.error('set_shared() invalid value provided: {0!s}'.format(share_str))
            return 'error'  # no change to database.

    @property
    def ft_link(self):
        return 'https://www.google.com/fusiontables/DataSource?docid=' + self.ft_docid

    def cell_list(self):
        """
        returns a cached list of the area's cells as a json dictionary
        """
        cell_list = []
        for cell_key in self.cells:
            cell = cell_key.get()
            if cell is not None:
                celldict = cell.Cell2Dictionary()
                if celldict is not None:
                    celldict['index'] = len(cell_list)
                    cell_list.append(celldict)
            else:
                logging.error("AreaofInterest::cell_list() no cell returned from key %s ", cell_key)

        returnstr = 'AreaofInterest::cell_list() area {0!s} has cells {1!s}'.format(self.name.encode('utf-8'),
                                                                                    cell_list)
        logging.debug(returnstr)

        return cell_list

    def count_monitored_cells(self):
        """
        returns the count of the area's landsat cells that are monitored.
        """
        cell_count = 0
        for cell_key in self.cells:
            cell = cell_key.get()
            if cell is not None:
                if cell.monitored == True:
                    cell_count += 1
            else:
                logging.error("AreaofInterest::count_monitored_cells() no cell returned from key %s ", cell_key)
                return -1
        logging.debug("AreaofInterest::count_monitored_cells()=%d", cell_count)
        return cell_count

    def delete_cells(self):
        # for cell_key in self.cells:
        #    cell_key.delete()
        ndb.delete_multi()
        self.cells = []

    def summary_dictionary(self):  # main parameters included for list of areas.
        return {
            'id': self.key.urlsafe(),  # unique id for this area.
            'url': self.url(),  # url to view area
            'tasks_url': self.tasks_url(),  # url to view tasks for this area
            'name': self.key.string_id().decode('utf-8'),
            'owner': self.owner.string_id(),
            'created_date': self.created_date,
            'num_followers': self.num_followers,
            'share': self.share
        }

    def area_location_as_geojson(self):
        """
        @return area_location as a Feature in geojson dictionary format or none
        """
        if self.area_location == None:
            logging.error('area_location_as_geojson(): No location for area')
            return None
        location_geojsonobj = {
            "type": "Feature",  # area locator point.
            "geometry": {
                "type": "Point",
                "coordinates": [self.area_location.lon, self.area_location.lat],
            },
            "properties": {
                "name": "area_location",
                "descr": 'user specified center location of area',
            }
        }
        return location_geojsonobj

    def toGeoJSON(self):

        """
        returns area as a geojson dictionary
        After http://google-app-engine-samples.googlecode.com/svn-history/r4/trunk/geodatastore/jsonOutput
        """

        geojson_obj = {
            "type": "FeatureCollection",
            "properties": {
                "area": self.name,
                "area_name": self.name,
                "shared": self.shared_str,
                "area_url": self.url(),
                'owner': self.owner.string_id(),  # area owner.
                "area_description": {
                    "description": self.description,
                    "description_why": self.description_why,
                    "description_who": self.description_who,
                    "description_how": self.description_how,
                    "wiki": self.wiki,
                    "threats": self.threats
                },
                "fusion_table": {
                    # "ft_link": self.ft_link(),
                    # "boundary_fc": self.boundary_fc,
                    "ft_docid": self.ft_docid
                },
                "stroke": "#550055",
                "stroke-width": 2,
                "stroke-opacity": 1,
                "fill": "#550055",
                "fill-opacity": 0.5
            },
            "features": [
                {"type": "Feature",  # map view
                 "geometry": {
                     "type": "Point",
                     "coordinates": [self.map_center.lon, self.map_center.lat],
                 },
                 "properties": {
                     "name": "mapview",
                     "zoom": self.map_zoom
                 }
                 },

            ]
        }
        location_geojson = self.area_location_as_geojson()
        if location_geojson != None:
            geojson_obj['features'].append(location_geojson)

        if self.boundary_hull != None:
            geojson_obj['features'].append(json.loads(self.boundary_hull))

        if self.boundary_geojsonstr != None:
            geojson_obj['boundary_geojson'] = json.loads(self.boundary_geojsonstr)

        return geojson_obj

    def hasBoundary(self):
        """
        @return True if area has a boundary. returns false if only a location is defined.
        """
        if self.boundary_hull == None or self.boundary_hull == "":
            return False
        else:
            return True

    def get_boundary_hull_fc(self):
        """
        @return the area's boundary hull or location as a FeatureCollection, or None on error.
        """
        if self.hasBoundary() == True:
            try:
                park_geom = json.loads(self.boundary_hull)
                # return boundary_hull_dict['geometry'] #['coordinates'] # outer ring only
                #print 'get_boundary_hull_fc loading boundary'
                # park_geom = ee.Geometry(boundary_hull_dict['geometry'])
            except KeyError:
                logging.error('get_boundary_hull_fc(): KeyError in boundary_hull')
                return None
            except ValueError:
                logging.error('get_boundary_hull_fc(): ValueError in boundary_hull')
                return None
            logging.debug('get_boundary_hull_fc %s boundary defined by polygon', self.name)
        else:

            if self.area_location == None:
                logging.error('get_boundary_hull_fc(): Area %s has neither boundary nor location', self.name)
                return None
            logging.debug('get_boundary_hull_fc %s boundary defined by point', self.name)
            park_geom = self.area_location_as_geojson()
            # ee.Geometry.Point([self.area_location.lon, self.area_location.lat])

        eeFeatureCollection, status, errormsg = self.get_eeFeatureCollection(park_geom)
        if eeFeatureCollection == None:
            logging.error("boundaryFC(%s) error %s object status %s input: %s", self.name, errormsg, status, park_geom)
        return eeFeatureCollection

    def set_boundary_fc(self, eeFeatureCollection, set_view):
        """
        Given an arbitrary ee.FeatureCollection, set area's boundary to a convex hull of the collections geometry.
        @param eeFeatureCollection an ee.FeatureCollection of arbitrary geometries.
        @param set_view Optionally sets the mapview and area-Location if True 
        @attention: updates area but does not store value in NDB.
        Sets:         
            self.boundary_hull (a geojson string)
            self.bounds 
            self.maxlatlon
            self.minlatlon
            self.total_area

        Optionally sets:
            self.map_center
            self.area_location
            
        @return boundary_hull as a dictionary - not a string
        """
        hull = eeFeatureCollection.geometry().convexHull(10);

        hull_coords = hull.coordinates().getInfo()
        # print coord_list
        boundary_hull_dict = {
            "type": "Feature",
            "properties": {
                "name": "boundary_hull",
                "stroke": "#555555",
                "stroke-width": 2,
                "stroke-opacity": 1,
                "fill": "#555555",
                "fill-opacity": 0.5
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": hull_coords
            }
        }
        self.boundary_hull = json.dumps(boundary_hull_dict)
        # self.boundary_fc = ee.FeatureCollection(hull).getInfo() # create a new FC based on the hull geometry.

        bounds = hull.bounds(10)
        rectangle = bounds.coordinates().getInfo()
        if len(rectangle):
            self.maxlatlon = ndb.GeoPt(float(rectangle[0][2][1]), float(rectangle[0][2][0]))
            self.minlatlon = ndb.GeoPt(float(rectangle[0][0][1]), float(rectangle[0][0][0]))

        self.total_area = hull.area(10).getInfo() / 1e6  # area in sq km

        if set_view == True:
            centroid = hull.centroid(10).coordinates().getInfo()

            self.map_center = ndb.GeoPt(float(centroid[1]), float(centroid[0]))
            self.area_location = self.map_center

        return boundary_hull_dict

    def get_fusion_boundary(self, boundary_ft):
        """
        Given a fusion table id query its geometry and save to an eeFeatureCollection
            @TODO move this function to eeservice as it does not use or set area.     
        #boundary_ft = new_area['properties']['fusion_table']['boundary_ft']
        #ftlink = 'https://www.google.com/fusiontables/DataSource?docid=' + boundary_ft
        #@TODO ftlink does not beed to be stroed as it can be returned by a function.
        #logging.debug('AreaHandler name: %s has fusion boundary:%s', boundary_ft)
        ### User Provided a Fusion Table ###
        #Test the fusion table
        #authenticate to fusion table API
        """
        # coords = []
        # total_area = 0
        # zoom = 12 # zoom will be calculated when the map is displayed.

        http = eeservice.EarthEngineService.credentials.authorize(httplib2.Http(memcache))
        service = build('fusiontables', 'v2', http=http)

        result = service.column().list(tableId=boundary_ft).execute(http)
        message = "Fusion Table Columns: "
        cols = result.get('items', [])

        geo_col_name = ""
        for c in cols:
            message += c['name']
            if c['type'] == 'LOCATION':
                message += '[LOCATION]'
                geo_col_name = c['name']
            message += "; "
        logging.debug("results=%s, column names=%s", result, message)

        # Fusion table EE operations tested at https://ee-api.appspot.com/b8dec39252c0eced49bb085f2b6fcdd4
        # make a convex hull and store the coordinates in ndb.model.AreoOfInterest.coords

        park_boundary_fc = ee.FeatureCollection(u'ft:' + boundary_ft, 'geometry')
        self.boundaryFromFC(park_boundary_fc)
        self.set_boundary_fc(self, park_boundary_fc, True)

    @staticmethod
    def get_eeFeatureCollection(geojson_obj):
        """ Creates an Earth Engine FeatureCollection from a geojson object containing a Feature or FeatureCollection. 
        First ensure the App is connected to the EarthEngine API or you get 404
        Does not modify the area object.
        
        @param geojson - A geojson object containing features
        @returns eeFeatureCollection, status, errormsg
        """

        eeFeatureCollection = None

        if not eeservice.initEarthEngineService():  # we need earth engine now.
            return eeFeatureCollection, 503, "Google Earth Engine service not available"

        eeFeatures = []
        # print 'geojson_obj: ', geojson_obj

        try:
            object_type = geojson_obj['type']

        except (ValueError, KeyError):
            return eeFeatureCollection, 400, "geojson object does not have a type expected Feature or FeatureCollection"

        try:
            if object_type == 'Feature':
                eeFeature = ee.Feature(geojson_obj['geometry'])
                cw_geom = eeFeature.geometry()
                ccw_geom = cw_geom.buffer(0, 1e-10)  # force polygon to be CCW so search intersects with interior.
                eeFeature = ee.Feature(ccw_geom, {'name': geojson_obj['properties']['name'], 'fill': 1})
                eeFeatures.append(eeFeature)

            elif object_type == 'FeatureCollection':
                features = geojson_obj['features']
                if len(features) == 0:
                    return eeFeatureCollection, 400, "geojson FeatureCollection contains no Featuress"

                for feature in features:
                    eeFeature = ee.Feature(feature['geometry'])
                    cw_geom = eeFeature.geometry()
                    ccw_geom = cw_geom.buffer(0, 1e-10)  # force polygon to be CCW so search intersects with interior.
                    eeFeature = ee.Feature(ccw_geom, {'name': "Border FeatureCollection", 'fill': 1})
                    eeFeatures.append(eeFeature)
            else:
                return None, 400, "geojson not a Feature or FeatureCollection"

            return ee.FeatureCollection(eeFeatures), 200, "object_type"

        except ee.EEException:
            return None, 500, "EEException creating FeatureCollection"

    def getBoundary(self, geojsonBoundary):
        """
        Given a boundary as a geojson Feature, apply it to the area.
        @deprecated:         Not Used.
        """
        if not eeservice.initEarthEngineService():  # we need earth engine now.
            self.response.set_status(503)
            return self.response.out.write('Sorry, Google Earth Engine not available right now. Please try again later')
            return [], 0, ndb.GeoPt(0, 0), ndb.GeoPt(0, 0)

        coords = []
        tmax_lat = -90
        tmin_lat = +90
        tmax_lon = -180
        tmin_lon = +180

        # convert the geojson polygon to NDB GeoPt()s
        # The list of coordinates for Polygons is nested one more level than that for LineStrings.
        # We only want the first set of coords - as this is the exterior ring.

        polygon = geojsonBoundary['geometry']['coordinates']
        for p in polygon[0]:
            lat = p[1]
            lon = p[0]
            #print lat, lon
            gp = ndb.GeoPt(float(lat), float(lon))
            coords.append(gp)

            # get bounds of area.
            if lat > tmax_lat:
                tmax_lat = lat
            if lat < tmin_lat:
                tmin_lat = lat
            if lon > tmax_lon:
                tmax_lon = lon
            if lon < tmin_lon:
                tmin_lon = lon

        maxlatlon = ndb.GeoPt(float(tmax_lat), float(tmax_lon))
        minlatlon = ndb.GeoPt(float(tmin_lat), float(tmin_lon))

        ### generate a FeatureCollection and calculate its area
        if len(coords) > 0:
            polypoints = []
            for geopt in coords:
                polypoints.append([geopt.lon, geopt.lat])
            cw_geom = ee.Geometry.Polygon(polypoints)
            ccw_geom = cw_geom.buffer(0, 1e-10)  # force polygon to be CCW so search intersects with interior.
            feat = ee.Feature(ccw_geom, {'name': 'not_used', 'fill': 1})

            total_area = ccw_geom.area().getInfo() / 1e6  # area in sq km

            # park_boundary_fc = ee.FeatureCollection(feat)
        else:
            # no boundary defined yet
            total_area = 0

        return coords, total_area, maxlatlon, minlatlon


class LandsatCell(ndb.Model):
    """
    Landsat Cell represents an 170sq km area where each image is captured. 
    Each path and row identifies a unique cell.
    An AOI makes overlaps a set of one or more cells - and creates a constant list of these.
    Each cell has a different schedule when new images arrive.
    
    Note that multiple LandsatCell objects for the same Landsat Cell(p,r) can be created, one for each parent area to which it belongs.
    
    The normal name for a Cell is a Swath.                
    """
    # constants - not changed once created. Created when AOI is created.
    path = ndb.IntegerProperty(required=True, default=0)  # Landsat Path
    row = ndb.IntegerProperty(required=True, default=0)  # Landsat Row
    aoi = ndb.KeyProperty(kind=AreaOfInterest)  # key to area that includes this cell

    # center = ndb.GeoPtProperty(required=False, default=None) # Geographic Center of Cell - not set or used.
    # bound = ndb.ListProperty(float, default=None)            # Geographic Boundary of Cell- not set or used

    overlap = ndb.FloatProperty(required=False)  # What proportion of this cell overlaps the AOI (>0, <=1).
    image_id = ndb.StringProperty(required=False)  # An ID of a Landsat image for this cell (may not be latest)

    monitored = ndb.BooleanProperty(required=True,
                                    default=False)  # Set if cell is monitored for new data (i.e selected in view-area)

    '''
    Cell2Dictionary() converts a cell object into a dictionary of the path,row, monitored 
    and date of latest image stored in
     datastore for L8 collection (other collections to follow)
    '''

    def Cell2Dictionary(self):
        # cell_list.append({"path":cell.path, "row":cell.row, "monitored":cell.monitored})

        celldict = {"path": int(self.path), "row": int(self.row), "monitored": "false", "LC8_latest_capture": "none",
                    "result": "ok"}

        if self.monitored:
            celldict['monitored'] = "true"
        q = self.latestObservation('LANDSAT/LC8_L1T_TOA')
        # print 'latestObservation ', q
        if q is not None:  # and len(q) <> 0:
            celldict['LC8_latest_capture'] = q.captured.strftime("%Y-%m-%d @ %H:%M")
        return celldict

    def latestObservation(self, collectionName="L8"):  # query for latest observation from given imageCollection.
        q = Observation.query(Observation.image_collection == collectionName, ancestor=self.key).order(
            -Observation.captured).fetch(1)
        if q is not None and len(q) <> 0:
            return q[0]
        else:
            return None

    @staticmethod
    def get_cell(path, row, area_name):
        if area_name is not None:
            # area_key = AreaOfInterest.query(AreaOfInterest.name == area_name.decode('utf-8')).fetch(keys_only=True)
            cell_name = str(path * 1000 + row)
            cell_key = ndb.Key('AreaOfInterest', area_name.decode('utf-8'), 'LandsatCell', cell_name)
            return cell_key.get()  # LandsatCell.get_by_id(cell_name,  parent=area_key)
        else:
            return LandsatCell.query().filter(LandsatCell.path == int(path)).filter(LandsatCell.row == int(row)).get()


'''
class Overlay describes a visualisation of an image asset.
It includes the map_id and token, an algorithm and information about the type. 
Used for a (Landsat) satelite image that has been retrieved and converted to a usable (visible/NDVI) format.
The image is based on an Observatioin Asset.
Note that the Overlay is an asset in the earth engine that has a limited expiry date.
If the tiles returned are 404 then it is necessary to recreate the overlay.
'''


class Overlay(ndb.Model):
    map_id = ndb.StringProperty(required=False, default=None)  # RGB Map Overlay Id generated in GEE -
    token = ndb.StringProperty(required=False, default=None)  # RGB Map Overlay Token might have expired.
    algorithm = ndb.StringProperty(
        required=False)  # identifies how the image was created - e.g. NDVI, RGB etc. #TODO How to specify this.
    overlay_role = ndb.StringProperty(
        required=False)  # Purpose of this asset for the task. expected values: 'LATEST', 'PREVIOUS'.

    def Overlay2Dictionary(self):
        obsdict = {
            "map_id": self.map_id,
            "token": self.token,
            "algorithm": self.algorithm,
            "overlay_role": self.overlay_role,
            "parent": str(self.key.parent()),
            "key": self.key.urlsafe()
        }
        return obsdict

    @staticmethod  # make it static so ndb recognises the kind='Overlay'
    def get_from_encoded_key(encoded_key):
        ovl_key = ndb.Key(urlsafe=encoded_key)
        if not ovl_key:
            logging.error('Overlay:get_from_encoded_key() -  could not read key in url')
            return None
        ovl = ovl_key.get()
        if not ovl:
            logging.error('Overlay:get_from_encoded_key() -  no overlay from urlkey')
            return None
        return ovl


'''
class Observation (could rename to ObservationAsset) describes a Landsat satellite image.

An Observation contains a list of zero or more Overlays, each Overlay is a visualization of the ObservationAsset.

The main use is the captured date. Once this observation has been actioned, it becomes the latest, against which future observations are base-lined for change detection.
This allows the app to redraw the overlay computed by earth engine on a new browser session without recalculating it - providing the overlay token has not expired.
In which case, app will need to regenerate the observation.    

Some Observations have no image_id as they are composites of many images.
'''


class Observation(ndb.Model):
    image_collection = ndb.StringProperty(required=False)  # identifies the ImageCollection name, not an EE object.
    image_id = ndb.StringProperty(required=False)  # LANDSAT Image ID of Image - key to query EE.
    captured = ndb.DateTimeProperty(
        required=False)  # sysdate or date Image was captured - could be derived by EE from collection+image_id.
    obs_role = ndb.StringProperty(
        required=False)  # Purpose of this asset for the task. expected values: 'LATEST', 'PREVIOUS'.
    overlays = ndb.KeyProperty(repeated=True,
                               default=None)  # list of keys to overlays (visualisations of this observation asset)

    # landsatCell = ndb.ReferenceProperty(LandsatCell) #defer initialization to init to avoid forward reference to new class defined. http://stackoverflow.com/questions/1724316/referencing-classes-in-python - use parent instead.

    @staticmethod  # make it static so ndb recognises the kind='Observation'
    def get_from_encoded_key(encoded_key):
        obskey = ndb.Key(urlsafe=encoded_key)
        if not obskey:
            logging.error('Observation:get_from_encoded_key() -  could not read key in url')
            return None
        obs = obskey.get()
        if not obs:
            logging.error('Observation:get_from_encoded_key() -  no observation found from urlkey')
            return None
        return obs

    def Observation2Dictionary(self):
        obsdict = {
            "image_collection": self.image_collection,
            "image_id": self.image_id,
            "captured": self.captured.strftime("%Y-%m-%d @ %H:%M"),
            "obs_role": self.obs_role,  # ex 'latest'
            "encoded_key": self.key.urlsafe(),
            "overlays": []
        }
        # obsdict['encoded_key'] = self.key.urlsafe()
        for ovl_key in self.overlays:
            overlay = ovl_key.get()
            if overlay is not None:
                obsdict['overlays'].append(overlay.Overlay2Dictionary())
        return obsdict


'''
class Task is an observation task, based on a landsat image in an AOI. The task includes a user who is responsible for completing the task.
Each task has a unique ID.
'''


class ObservationTask(ndb.Model):
    OBSTASKS_PER_PAGE = 5
    # Observation
    name = ndb.StringProperty()
    aoi = ndb.KeyProperty(kind=AreaOfInterest)  # key to area that includes this cell

    # privacy and sharing
    share = ndb.IntegerProperty(required=True,
                                default=AreaOfInterest.PUBLIC_AOI)  # set to hide area. see @properties below
    aoi_owner = ndb.KeyProperty(
        kind=User)  # ,collection_name='aoi_owner') #owner of the aoi- not the volunteer assigned to task. Allows quicker filtering of private areas..

    observations = ndb.KeyProperty(
        repeated=True)  # key to observations related to this task. E.g if two images are in the same path and published at same time.

    # people -     Expected to be a user  who is one of the area's followers. volunteering to follow the AOI
    assigned_owner = ndb.KeyProperty(
        kind=User)  # , collection_name='assigned_owner') # user who is currently assigned the the task
    # original_owner = ndb.KeyProperty(kind=User) #, collection_name='original_user') # user originally assigned the the task -

    # timestamps
    created_date = ndb.DateTimeProperty(auto_now_add=True)
    last_modified = ndb.DateTimeProperty(auto_now=True)

    # workflow
    status = ndb.StringProperty()  # Task's workflow
    priority = ndb.IntegerProperty()  # Task's priority - zero is highest priority. Other followers may be given same task but at a lower priority.

    # TODO: add list of references to reports
    # TODO: add an Activity record.

    @property
    def pages(self, obstask_count):
        if obstask_count == 0:
            return 1
        return (obstask_count + self.OBSTASK_PER_PAGE - 1) / self.OBSTASKS_PER_PAGE

    def taskurl(self):
        username = self.assigned_owner.string_id()
        task_id = self.key.id()
        # print 'taskurl: ', username, ' task id: ', task_id
        return webapp2.uri_for('view-obstask', task_id=task_id)

    def listurl(self, page=1, username=None):  # show a list of recent tasks
        # logging.debug("listurl %s ", username )
        if page > 1:
            return webapp2.uri_for('view-obstasks', username=username, user2view=self.assigned_owner.name,
                                   task_name=self.key.id(), page=page)
        else:
            return webapp2.uri_for('view-obstasks', username=username, user2view=self.assigned_owner.name,
                                   task_name=self.key.id())

    def shared_str(self):
        if self.share == AreaOfInterest.PUBLIC_AOI:
            return 'public'
        elif self.share == AreaOfInterest.UNLISTED_AOI:
            return 'unlisted'
        elif self.share == AreaOfInterest.PRIVATE_AOI:
            return 'private'
        else:
            return 'unspecified'


'''
A Journal consists of user entries. Journals used for recording observations from tasks are a special class as they also record the image id.
Based on journalr.org 
'''


class Journal(ndb.Model):
    ENTRIES_PER_PAGE = 5
    MAX_JOURNALS = 100

    journal_type = ndb.StringProperty(required=True, default="journal")  # "journal", "observations", "reports" etc.
    # name = ndb.StringProperty(required=True) # with ndb can use id now
    created_date = ndb.DateTimeProperty(auto_now_add=True)
    last_entry = ndb.DateTimeProperty()
    first_entry = ndb.DateTimeProperty()
    last_modified = ndb.DateTimeProperty(auto_now=True)
    entry_count = ndb.IntegerProperty(required=True, default=0)
    entry_days = ndb.IntegerProperty(required=True, default=0)

    chars = ndb.IntegerProperty(required=True, default=0)
    words = ndb.IntegerProperty(required=True, default=0)
    sentences = ndb.IntegerProperty(required=True, default=0)

    # all frequencies are per week
    freq_entries = ndb.FloatProperty(required=True, default=0.)
    freq_chars = ndb.FloatProperty(required=True, default=0.)
    freq_words = ndb.FloatProperty(required=True, default=0.)
    freq_sentences = ndb.FloatProperty(required=True, default=0.)

    def count(self):
        if self.entry_count:
            self.entry_days = (self.last_entry - self.first_entry).days + 1
            weeks = self.entry_days / 7.
            self.freq_entries = self.entry_count / weeks
            self.freq_chars = self.chars / weeks
            self.freq_words = self.words / weeks
            self.freq_sentences = self.sentences / weeks
        else:
            self.entry_days = 0
            self.freq_entries = 0.
            self.freq_chars = 0.
            self.freq_words = 0.
            self.freq_sentences = 0.

    def __unicode__(self):
        return unicode(self.key.string_id)


    @property
    def name(self):
        return self.key.string_id()

    @property
    def pages(self):
        if self.entry_count == 0:
            return 1
        return (self.entry_count + self.ENTRIES_PER_PAGE - 1) / self.ENTRIES_PER_PAGE

    def url(self, page=1):
        if page > 1:
            return webapp2.uri_for('view-journal', username=self.key.parent().string_id().decode('utf-8'),
                                   journal_name=self.key.string_id(), page=page)
        else:
            return webapp2.uri_for('view-journal', username=self.key.parent().string_id(),
                                   journal_name=self.key.string_id().decode('utf-8'))

    @staticmethod
    def get_journal(username, journal_name):
        user_key = ndb.Key('User', username)
        journal_key = ndb.Key('Journal', journal_name, parent=user_key)
        # print 'journal_key', journal_key
        return journal_key.get()


RENDER_TYPE_HTML = 'HTML'
RENDER_TYPE_MARKDOWN = 'markdown'
RENDER_TYPE_RST = 'reStructured Text'
RENDER_TYPE_TEXT = 'plain text'
RENDER_TYPE_TEXTILE = 'textile'

CONTENT_TYPE_CHOICES = [
    RENDER_TYPE_MARKDOWN,
    RENDER_TYPE_RST,
    RENDER_TYPE_TEXT,
    RENDER_TYPE_TEXTILE,
]


class EntryContent(ndb.Model):
    subject = ndb.StringProperty()
    tags = ndb.StringProperty(repeated=True)
    text = ndb.TextProperty()
    rendered = ndb.TextProperty(default='')
    markup = ndb.StringProperty(required=True, indexed=False, choices=CONTENT_TYPE_CHOICES, default=RENDER_TYPE_TEXT)
    images = ndb.StringProperty(repeated=True)

    @staticmethod
    def get_entrycontent_key(journal):
        entry_id = Entry.allocate_ids(1)[
            0]  # return next available entry_id inside parents space. [0] is start, [1] is end.
        return ndb.Key('EntryContent', entry_id, parent=journal.key)


class Entry(ndb.Model):
    date = ndb.DateTimeProperty(auto_now_add=True)
    created = ndb.DateTimeProperty(required=True, auto_now_add=True)
    last_edited = ndb.DateTimeProperty(required=True, auto_now=True)

    content = ndb.IntegerProperty(required=True)  # key id of EntryContent
    blobs = ndb.StringProperty(repeated=True)

    chars = ndb.IntegerProperty(required=True, default=0)
    words = ndb.IntegerProperty(required=True, default=0)
    sentences = ndb.IntegerProperty(required=True, default=0)

    google_docs_id = ndb.StringProperty(indexed=False)

    WORD_RE = re.compile("[A-Za-z0-9']+")
    SENTENCE_RE = re.compile("[.!?]+")

    @property
    def time(self):
        if not self.date.hour and not self.date.minute and not self.date.second:
            return ''
        else:
            return self.date.strftime('%H:%M')

    @property
    def content_key(self):
        return ndb.Key('EntryContent', long(self.content), parent=self.key)

    @property
    def blob_keys(self):
        return [ndb.Key('Blob', long(i), parent=self.key) for i in self.blobs]

    @staticmethod
    def get_entry_key(journal, entry_id=None):
        if entry_id == None:
            entry_id = Entry.allocate_ids(1)[
                0]  # return next available entry_id inside parents space. [0] is start, [1] is end.
        return ndb.Key('Entry', long(entry_id), parent=journal.key)

    @staticmethod
    def get_entry(username, journal_name, entry_id, entry_key=None):
        journal = Journal.get_journal(username, journal_name)
        if not journal:
            logging.error('Entry.get_entry(): Error no journal called %s for user %s', journal_name, username)
            assert journal
            return None, None, None
        if not entry_key:
            # logging.debug('Entry.get_entry(): Error no entry_id %s, for user %s, journal %s', entry_id, username, journal_name )
            entry_key = Entry.get_entry_key(journal, entry_id)  # get_entry_key(username, journal_name, entry_id)
        if not entry_key:
            return None, None, None
        entry = entry_key.get()

        if entry:
            content = EntryContent.get_by_id(long(entry.content), parent=journal.key)
            if not content:
                logging.error('Entry.get_entry(): Error. No content')
            if entry.blobs:
                blobs = ndb.get_multi(entry.blob_keys)
            else:
                blobs = []
            return entry, content, blobs
        else:
            return None, None, []

    @staticmethod
    def get_entries(journal, latestFirst=True):
        q = Entry.query(ancestor=journal.key)
        if latestFirst:
            entries = q.order(-Entry.date)
        else:
            entries = q.order(Entry.date)
        return entries.fetch(2)


ACTIVITY_NEW_JOURNAL = 1
ACTIVITY_NEW_ENTRY = 2
ACTIVITY_FOLLOWING = 3
ACTIVITY_SAVE_ENTRY = 4
ACTIVITY_NEW_AREA = 5
ACTIVITY_NEW_OBS = 6
ACTIVITY_NEW_REPORT = 7
ACTIVITY_NEW_FEEDBACK = 8
ACTIVITY_DELETE_AREA = 9
ACTIVITY_UNFOLLOWING = 10

ACTIVITY_CHOICES = [
    ACTIVITY_NEW_JOURNAL,
    ACTIVITY_NEW_ENTRY,
    ACTIVITY_FOLLOWING,
    ACTIVITY_SAVE_ENTRY,
    ACTIVITY_NEW_AREA,
    ACTIVITY_NEW_OBS,
    ACTIVITY_NEW_REPORT,
    ACTIVITY_NEW_FEEDBACK,
    ACTIVITY_DELETE_AREA,
    ACTIVITY_UNFOLLOWING
]

ACTIVITY_ACTION = {
    ACTIVITY_NEW_JOURNAL: 'created a new journal',
    ACTIVITY_NEW_ENTRY: 'started a new journal entry',
    ACTIVITY_FOLLOWING: 'started following',
    ACTIVITY_SAVE_ENTRY: 'updated a journal entry',
    ACTIVITY_NEW_AREA: 'created a new area of interest',
    ACTIVITY_NEW_OBS: 'created a new observation',
    ACTIVITY_NEW_REPORT: 'created a new report',
    ACTIVITY_NEW_FEEDBACK: 'created new feedback',
    ACTIVITY_DELETE_AREA: 'deleted an area of interest',
    ACTIVITY_UNFOLLOWING: 'stopped following',
}


class Activity(DerefModel):
    RESULTS = 50

    user = ndb.StringProperty(required=True)
    img = ndb.StringProperty(indexed=False)
    date = ndb.DateTimeProperty(auto_now_add=True)
    action = ndb.IntegerProperty(required=True, choices=ACTIVITY_CHOICES)
    object = ndb.KeyProperty()

    def get_action(self):
        r = ACTIVITY_ACTION[self.action]

        if self.action in [ACTIVITY_FOLLOWING, ACTIVITY_UNFOLLOWING]:
            obj= self.object.get()
            if obj:
                r += ' <a href="%s">%s</a>' % (obj.url(), obj.name)

        if self.action in [ACTIVITY_NEW_JOURNAL]:
            obj= self.object.get()
            if obj:
                r += ' <a href="%s">%s</a>' % (obj.url(), self.object.string_id().decode('utf-8'))

        #if self.action == ACTIVITY_NEW_JOURNAL:
        #    journal= self.object.get()
        #    name = self.object.string_id().decode('utf-8')
        #    r += ' <a href="%s">%s</a>' % (journal.url(), journal.name)

        return r

    @staticmethod
    def create(user, action, activity):
        a = Activity(user=user.name, img=user.gravatar('30'), action=action, object=activity)
        ar = a.put()

        # receivers = cache.get_followers(user.name)
        # receivers.append(user.name)
        # a.get_result()

        # ai = ActivityIndex(parent=a, receivers=receivers)
        ai = ActivityIndex(parent=ar)
        ai.put()


class ActivityIndex(ndb.Model):
    receivers = ndb.StringProperty(repeated=True)
    date = ndb.DateTimeProperty(auto_now_add=True)


BLOB_TYPE_IMAGE = 1
BLOB_TYPE_PDF = 2

BLOB_TYPE_CHOICES = [
    BLOB_TYPE_IMAGE,
    BLOB_TYPE_PDF,
]


class Blob(DerefExpando):
    MAXSIZE = 4 * 2 ** 20  # 4MB

    blob = ndb.BlobKeyProperty(required=True)
    type = ndb.IntegerProperty(required=True, choices=BLOB_TYPE_CHOICES)
    name = ndb.StringProperty(indexed=False)
    size = ndb.IntegerProperty()
    url = ndb.StringProperty(indexed=False)

    def get_url(self, size=None, name=None):
        if self.type == BLOB_TYPE_IMAGE:
            if not self.url:
                self.url = images.get_serving_url(self.blob)

            url = self.url

            if size:
                url += '=s' + size

            return url
        else:
            kwargs = {'key': self.get_key('blob')}

            if name is True:
                name = self.name
            if name:
                kwargs['name'] = name

            return webapp2.uri_for('blob', **kwargs)

    @staticmethod
    def get_blob_key(entry, blob_id=None):
        if blob_id == None:
            blob_id = Blob.allocate_ids(1)[
                0]  # return next available entry_id inside parents space. [0] is start, [1] is end.
        return ndb.Key('Blob', long(blob_id), parent=entry.key)

    @staticmethod
    def get_blob(username, journal_name, entry_id, entry_key=None):
        journal = Journal.get_journal(username, journal_name)

        if not entry_key:
            entry_key = Entry.get_entry_key(journal, entry_id)  # get_entry_key(username, journal_name, entry_id)
        entry = entry_key.get()

        if entry:
            content = EntryContent.get_by_id(long(entry.content), parent=journal.key)
            if not content:
                logging.error('Entry.get_entry(): Error. No content')
            if entry.blobs:
                blobs = ndb.get_multi(entry.blob_keys)
            else:
                blobs = []
            return entry, content, blobs
        else:
            return None

            # handmade_key = ndb.Key('Blob', 1, parent=entry_key)
        # blob_id = ndb.allocate_ids(handmade_key, 1)[0]
        # blob_key = ndb.Key.from_path('Blob', blob_id, parent=entry_key)
        blob_key = Blob.get_blob_key(entry)
        new_blob = Blob(key=blob_key, blob=blob, type=blob_type, name=blob.filename, size=blob.size)


RENDER_TYPE_CHOICES = [
    RENDER_TYPE_HTML,
    RENDER_TYPE_MARKDOWN,
    RENDER_TYPE_RST,
    RENDER_TYPE_TEXT,
    RENDER_TYPE_TEXTILE,
]


class BlogEntry(ndb.Model):
    ENTRIES_PER_PAGE = 10

    date = ndb.DateTimeProperty(required=True, auto_now_add=True)
    draft = ndb.BooleanProperty(required=True, default=True)
    markup = ndb.StringProperty(required=True, indexed=False, choices=RENDER_TYPE_CHOICES, default=RENDER_TYPE_MARKDOWN)
    title = ndb.StringProperty(required=True, indexed=False, default='Title')
    text = ndb.TextProperty(default='')
    rendered = ndb.TextProperty(default='')
    user = ndb.StringProperty(required=True)
    avatar = ndb.StringProperty()
    slug = ndb.StringProperty(indexed=False)

    @property
    def url(self):
        if not self.slug:
            self.slug = str(self.key.urlsafe())

        return webapp2.uri_for('blog-entry', entry=self.slug)


class Config(ndb.Expando):
    pass
