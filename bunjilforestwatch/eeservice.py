'''
Wrappers for some Earth Engine Routines
Created on 25/05/2013
@author: cgoodman

'''
import sys
import logging
logging.basicConfig(level=logging.DEBUG)

import os
from os import environ
#logging.debug('PYTHONPATH: %s',os.environ['PYTHONPATH'])
#logging.debug('HTTP_PROXY: %s',os.environ['HTTP_PROXY'])
#logging.debug('HTTPS_PROXY: %s',os.environ['HTTPS_PROXY'])

#if os.environ['EARTHENGINE_BYPASS'].startswith('T'): 
#	logging.info('EARTHENGINE_BYPASS is %s. Earth Engine Calls are disabled..',os.environ['EARTHENGINE_BYPASS'])

import oauth2client.client
from oauth2client.appengine import AppAssertionCredentials

import datetime
import json

import settings
#You have to create your own keys. 

#if os.environ['EARTHENGINE_BYPASS'].startswith('T'): 
#	logging.info('EARTHENGINE_BYPASS is %s. Earth Engine Calls are disabled..',os.environ['EARTHENGINE_BYPASS'])
#else:
import ee
	

'''
initEarthEngineService()
Call once per session to authenticate to EE
SERVER_SOFTWARE: In the development web server, this value is "Development/X.Y" where "X.Y" is the version of the runtime. 
When running on App Engine, this value is "Google App Engine/X.Y.Z".

'''
earthengine_intialised = False

def initEarthEngineService():

	#SCOPES = ('https://www.googleapis.com/auth/earthengine.readonly') # still needed?

	global earthengine_intialised
	if earthengine_intialised == False:
		try:
			if os.environ['SERVER_SOFTWARE'].startswith('Development'): 
				logging.info("Initialising Earth Engine authenticated connection from devserver")
		 		EE_CREDENTIALS = ee.ServiceAccountCredentials(settings.MY_LOCAL_SERVICE_ACCOUNT, settings.MY_LOCAL_PRIVATE_KEY_FILE)
		  	else:
		 		logging.info("Initialising Earth Engine authenticated connection from App Engine")
		 		EE_CREDENTIALS = AppAssertionCredentials(ee.OAUTH2_SCOPE)
			ee.Initialize(EE_CREDENTIALS) 
			earthengine_intialised = True
		except Exception, e:
			#self.add_message('error', 'An error occurred with Earth Engine. Try again.')
			logging.error("Failed to connect to Earth Engine. Exception: %s", e)
			pass

'''
getLandsatImage(array of points, string as name of ee.imagecollection)
returns the 'depth' latest image from the collection that overlaps the boundary coordinates.
Could also clip the image to the coordinates to reduce the size.
'''
secsperyear = 60 * 60 * 24 * 365 #  365 days * 24 hours * 60 mins * 60 secs

    
def getLatestLandsatImage(boundary_polygon, collection_name, latest_depth, opt_path = None, opt_row = None):
	#logging.info('boundary_polygon %s type: %s', boundary_polygon, type(boundary_polygon))
	feat = ee.Geometry.Polygon(boundary_polygon)
	#logging.info('feat %s', feat)
	boundary_feature = ee.Feature(feat, {'name': 'areaName', 'fill': 1})
	#boundary_feature_buffered = boundary_feature.buffer(0, 1e-10) # force polygon to be CCW so search intersects with interior.
	logging.debug('Temporarily disabled buffer to allow AOI points in clockwise order due to EEAPI bug')

	boundary_feature_buffered = boundary_feature 
	park_boundary = ee.FeatureCollection(boundary_feature_buffered)
	info = park_boundary.getInfo()
	end_date   = datetime.datetime.today()
	start_date = end_date - datetime.timedelta(seconds = 1 * secsperyear )
	logging.debug('start:%s, end:%s ',start_date,  end_date)

	image_collection = ee.ImageCollection(collection_name)
	sortedCollection = image_collection.filterBounds(park_boundary).filterDate(start_date, end_date).sort('system:time_start', False )
	resultingCollection = sortedCollection
	if opt_path is not None and opt_row is not None:
		#filter Landsat by Path/Row and date
		if ("L7" in collection_name):
			row_fieldname = "STARTING_ROW"
		else:
			row_fieldname = "WRS_ROW"
		resultingCollection = sortedCollection.filterMetadata('WRS_PATH', 'equals', opt_path).filterMetadata(row_fieldname, 'equals', opt_row)
	
	#logging.info('Collection description : %s', sortedCollection.getInfo())
  
	scenes  = resultingCollection.getInfo()
	#logging.info('Scenes: %s', sortedCollection)
	
	try:
		feature = scenes['features'][int(latest_depth)]
	except IndexError:
		feature = scenes['features'][0]
	
#	 #debugging loop through collection
#	 for x in range(0, len(scenes)):
#		 print ("x: ", x)
#		 feature = scenes['features'][x]
#		 idx = feature['id']
#		 imagex = ee.Image(idx)
#		 propsx = imagex.getInfo()['properties'] 
#		 system_time_startx= datetime.datetime.fromtimestamp(propsx['system:time_start'] / 1000) #convert ms
#		 date_strx = system_time_startx.strftime("%Y-%m-%d @ %H:%M")
#		 logging.info("loop: %d, %s", x, date_strx)	
	   #prints
	   #INFO	 2013-09-24 12:53:10,357 eeservice.py:76] loop: 0, 2013-09-03 @ 01:18
	   #INFO	 2013-09-24 12:53:13,052 eeservice.py:76] loop: 1, 2013-08-18 @ 01:18
	   #INFO	 2013-09-24 12:53:15,960 eeservice.py:76] loop: 2, 2013-08-02 @ 01:18
	   #INFO	 2013-09-24 12:53:18,596 eeservice.py:76] loop: 3, 2013-07-17 @ 01:18
	   #INFO	 2013-09-24 12:53:21,611 eeservice.py:76] loop: 4, 2013-07-01 @ 01:18
	   #INFO	 2013-09-24 12:53:24,493 eeservice.py:76] loop: 5, 2013-05-30 @ 01:18  
	
	id = feature['id']   
	#logging.info('getLatestLandsatImage found scene: %s', id)
	latest_image = ee.Image(id)
	props = latest_image.getInfo()['properties'] #logging.info('image properties: %s', props)
	test = latest_image.getInfo()['bands']
	#print (test)
	crs = latest_image.getInfo()['bands'][0]['crs']
	#path	= props['WRS_PATH']
	#row	 = props['STARTING_ROW']
	system_time_start= datetime.datetime.fromtimestamp(props['system:time_start'] / 1000) #convert ms
	date_str = system_time_start.strftime("%Y-%m-%d @ %H:%M")

	logging.info('getLatestLandsatImage id: %s, date:%s latest:%s', id, date_str, latest_depth )
	x = latest_image.getInfo()
	latest_image.name = id
	latest_image.capture_date = date_str
	x['mynewkey'] = id 
	return latest_image  #.clip(park_boundary)


def SharpenLandsat7HSVUpres(image):
        #Convert to HSV, swap in the pan band, and convert back to RGB. 
        #Javascript Example from https://ee-api.appspot.com/#5ea3dd541a2173702cfe6c7a88346475
        #Pan sharpen Landsat 8
        rgb = image.select(['30', '20', '10']).unitScale(0, 255) #Select the visible red, green and blue bands.
        pan = image.select(['80']).unitScale(0, 155)
        huesat = rgb.rgbtohsv().select(['hue', 'saturation'])
        upres = ee.Image.cat(huesat, pan).hsvtorgb()  
        byteimage = upres.multiply(255).byte()
        return(byteimage)    

def SharpenLandsat8HSVUpres(image):
        #Convert to HSV, swap in the pan band, and convert back to RGB. 
        #Javascript Example from https://ee-api.appspot.com/#5ea3dd541a2173702cfe6c7a88346475
        #Pan sharpen Landsat 8
        rgb = image.select("B4","B3","B2")
        pan = image.select("B8")
        huesat = rgb.rgbtohsv().select(['hue', 'saturation'])
        upres = ee.Image.cat(huesat, pan).hsvtorgb()  
        byteimage = upres.multiply(255).byte()
        newImage = image.addBands(byteimage); #keep all the metadata of image, but add the new bands.
        return(newImage)

# def getL8SharpImage(coords, depth): # wont use now
#     image = getLatestLandsatImage(coords, 'LANDSAT/LC8_L1T_TOA', depth)
#     sharpimage = SharpenLandsat8HSVUpres(image)
#     return sharpimage

###################################
# Image statistics

# Calculate the 5% and 95% values for each band in a Landsat image,
# and use them to construct visualization parameters for displaying the image.
#
# Example created: August 8, 2013
# NOTE: The syntax for the reducer objects is expected to change in the near future
#       so check the developers list if this example stops working.

# Return the percentile values for each band in an image.
def getPercentile(image, percentile, crs):
    return image.reduceRegion(
        ee.Reducer.percentile([percentile]), # reducer
        None, # geometry (Defaults to the footprint of the image's first band)
        None, # scale (Set automatically because bestEffort == true)
        crs,
        None, # crsTransform,
        True  # bestEffort
        ).getInfo()


def getL8LatestNDVIImage(image):
    NDVI_PALETTE = {'FF00FF','00FF00'}
    ndvi = image.normalizedDifference(["B4", "B3"]).median();   
    
    #addToMap(ndvi.median(), {min:-1, max:1}, "Median NDVI");
    #getMapId(ndvi, {min:-1, max:1, palette:NDVI_PALETTE}, "NDVI");
    
    newImage = image.addBands(ndvi); #keep all the metadata of image, but add the new bands.
    print('getL8NDVIImage: ', newImage)

    mapparams = {    #'bands':  'red, green, blue', 
                     'min': -1,
                     'max': 1,
                     'palette': 'FF00FF, 00FF00',
                     #'gamma': 1.2,
                     'format': 'png'
                }   
    mapid  = ndvi.getMapId(mapparams)
  
    # copy some image props to mapid for browser to display
    info = image.getInfo() #logging.info("info", info)
    props = info['properties']
    mapid['date_acquired'] = props['DATE_ACQUIRED']
    mapid['id'] = props['system:index']
    mapid['path'] = props['WRS_PATH']
    mapid['row'] = props['WRS_ROW']
    return mapid

def getVisualMapId(image, red, green, blue):
    #original image is used for original metadata lost in image so caller must keep a reference to the image
    crs = image.getInfo()['bands'][0]['crs']
    p05 = []
    p95 = []
    p05 = getPercentile(image, 5, crs)
    p95 = getPercentile(image, 95, crs) 
    min = str(p05[red]) + ', ' + str(p05[green]) + ', ' + str(p05[blue])
    max = str(p95[red]) + ', ' + str(p95[green]) + ', ' + str(p95[blue])
    print('Percentile  5%: ', min)
    print('Percentile 95%: ', max)
    # Define visualization parameters, based on the image statistics.
    mapparams = {    'bands':  'red, green, blue', 
                     'min': min,
                     'max': max,
                     'gamma': 1.2,
                     'format': 'png'
                }   
    mapid  = image.getMapId(mapparams)
    return mapid

# def getTiles(mapid): #not used
#     tilepath = ee.data.getTileUrl(mapid, 0, 0, 1)
#     logging.info('getTiles: %s',       tilepath)
#     return tilepath

# def GetMap(coords, depth): # not used except test
#         image = getLatestLandsatImage(coords, 'LANDSAT/LC8_L1T_TOA', depth)
#        
#         sharpimage = SharpenLandsat8HSVUpres(image)
#         #byteimage = sharpimage.multiply(255).byte()
#         red = 'red'
#         green = 'green'
#         blue = 'blue'
#         if False:
#             mapid = getVisualMapId(sharpimage, red, green, blue)
#         else:
#             mapid = getVisualMapId(image, red, green, blue)
#             
def getThumbnailPath(image):
        # GET THUMBNAIL
        crs = image.getInfo()['bands'][0]['crs']
        imgbands = image.getInfo()['bands']
        for b in imgbands:
            print b
        p05 = []
        p95 = []
        p05 = getPercentile(image, 5, crs)
        p95 = getPercentile(image, 95, crs)
        print('Percentile  5%: ', p05)
        print('Percentile 95%: ', p95)
        
        red = 'red'
        green = 'green'
        blue = 'blue'
        bands1 = [  {u'id': red},
                    {u'id': green},
                    {u'id': blue}   ]
        
        thumbnail_params = {
                     'bands': json.dumps(bands1),
                     #'crs': crs,
                     'format': 'png',
                     'size' : 2000,
                     'min': p05,
                     'max': p95,         
                     #'min': [p05['red'], p05['green'], p05['blue']],
                     #'max': [p95['red'], p95['green'], p95['blue']],                 
                     'gamma': 1.2,
                     }
        
        thumbpath = image.getThumbUrl(thumbnail_params)
        logging.info('thumbnail url: %s', thumbpath)
        return thumbpath

# Get a download URL for a GeoTIFF overlay.
def getOverlayPath(image, prefix, red, green, blue):

    crs = image.getInfo()['bands'][0]['crs']
    imgbands = image.get('bands')
    #for b in imgbands:
    #    print b
    # Get the percentile values for each band.
    p05 = []
    p95 = []
    p05 = getPercentile(image, 5, crs)
    p95 = getPercentile(image, 95, crs)
    # Print out the image ststistics.
    print('Percentile  5%: ', p05)
    print('Percentile 95%: ', p95)

    bands1 = [     {u'id': red},
                   {u'id': green},
                   {u'id': blue}   ]
    
    # Define visualization parameters, based on the image statistics.
    dt = datetime.datetime.now()
    filename = dt.strftime(prefix + "_%a%Y%b%d_%H%M")
    print filename
    visparams = {'name': filename,
                     'bands':  json.dumps(bands1), # none of the above work.
                     #'crs': crs,
                     #'format': 'png',
                     'min': p05,
                     'max': p95,
                     'gamma': 1.2,
                     #'scale': 30,
                     #'gain':  0.1, 
                     #'region' : boundary_polygon,    
                     'filePerBand' : False
                }   
    path      = image.getDownloadUrl(visparams)
    logging.info('getOverlayPath: %s',       path)
    return path

def getLandsatOverlay(coords, satellite, algorithm, depth):
    if satellite == 'l8':
        image = getLatestLandsatImage(coords, 'LANDSAT/LC8_L1T_TOA', depth)
        if algorithm == 'rgb':
            sharpimage = SharpenLandsat8HSVUpres(image)
            red = 'red'
            green = 'green'
            blue = 'blue'    
            #path = getOverlayPath(sharpimage, "L8TOA", red, green, blue)
            mapid = getVisualMapId(sharpimage, red, green, blue)
            info = image.getInfo() #logging.info("info", info)
            #print info
            props = info['properties']
            mapid['date_acquired'] = props['DATE_ACQUIRED']
            mapid['id'] = props['system:index']
            mapid['path'] = props['WRS_PATH']
            mapid['row'] = props['WRS_ROW']
            return mapid
        elif algorithm == 'ndvi':
            print "l8 ndvi"
            
    elif satellite == 'l7':
        image = getLatestLandsatImage(coords, 'LANDSAT/L7_L1T', depth)
        props = image.getInfo()['properties'] 
        if algorithm == 'rgb':
            sharpimage = SharpenLandsat7HSVUpres(image)
            red   = 'red'
            green = 'green'
            blue  = 'blue'    
            mapid = getVisualMapId(sharpimage, red, green, blue)
            mapid['date_acquired'] = props['DATE_ACQUIRED']
            mapid['id'] = props['system:index']
            mapid['path'] = props['WRS_PATH']
            mapid['row'] = props['WRS_ROW']
            
            return mapid
        
        elif algorithm == 'ndvi':
           print "l7 ndvi"




################# NOT USED ############################################

