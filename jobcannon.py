import boto3
from bs4 import BeautifulSoup
import re
import requests
import slack
import json
import os
import logging

# Slack
SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
SLACK_CHANNEL = os.environ['SLACK_CHANNEL']
botName = 'JobCannon'
botIconEmoji = ':cannon:'

# AWS/S3
S3_BUCKET = os.environ['S3_BUCKET']
KEY = 'previous_results.json'

# utexas SSO
LOGIN_URL = 'https://mba-mccombs-utexas-csm.symplicity.com/students/index.php?s=event&ss=is&_ksl=1&mode=list'
EID = os.environ['EID']
PASSWORD = os.environ['PASSWORD']

# create logger
logger = logging.getLogger(__name__)

def eid_login(login_url, eid, password):
	
	s = requests.Session()
	login = s.get(LOGIN_URL)
	
	login_html = BeautifulSoup(login.text, features="html.parser")

	hidden_inputs = login_html.find_all('input', type='hidden')
	form = {x["name"]: x["value"] for x in hidden_inputs}

	# add the email and password fields to the form
	form['IDToken1'] = EID
	form['IDToken2'] = PASSWORD

	# LARES DATA 
	response = s.post(login.url,data=form) #login.url is the last url of the redirect
	response_html = BeautifulSoup(response.text, features="html.parser")

	form_element = response_html.findAll(attrs={"name": "Response"})
	form_action = form_element[0]['action']

	hidden_inputs = response_html.find_all('input', type='hidden')
	form = {x["name"]: x["value"] for x in hidden_inputs}

	s.post(form_action,data=form)
	
	logger.info('logged in to SSO successfully')
	
	return s

def scrape_events(s):
	
	logger.info('start scraping events')
	new_page = s.get('https://mba-mccombs-utexas-csm.symplicity.com/students/index.php?_so_list_aate8fda65cf087d7272eb3273475b8ad24=250')
	soup = BeautifulSoup(new_page.text, features="html.parser")
	
	logger.info('start parsing events from page')
	results = []
	for result in soup.find_all("li",class_="list-item list_rows"): # this is the element which contains each event

		# exclusion filter
		if "MBA Career Management" in result.text: # exclude this event
			continue
		
		if "ABC Test Company" in result.text: # exclude test events
			continue		

		# set title
		title_tag = result.find("div",class_="list-item-title") # this is the event name element

		if title_tag:
			for string in title_tag.stripped_strings:
				title = string
		else:
			title = None

		# set event type
		event_type_tag = result.find("div",class_="list-secondary-action") # this is the event type element

		if event_type_tag:
			for string in event_type_tag.stripped_strings:
				event_type = string

		# set event datetime
		pattern = re.compile('Start|End|AM|PM')
		datetime_tag = result.find("span",class_="field-content",string=pattern) # this is the event time info element

		if datetime_tag:
			for string in datetime_tag.strings:
				datetime = string
		else:
			datetime = None

		# construct the dictonary for the event info using the previously extracted fields    
		eventInfo = {'title':title,
					 'eventType':event_type,
					 'datetime':datetime
					}
		
		# check if the discovered event is in the previous event array.
		# if event already in event array, then continue
		# if not, then add the event to the array
		if eventInfo in results:
			continue
		else:
			results.append(eventInfo)
	logger.info('done scraping events')       
	return results

def read_from_previous_results():
	
	logger.info('start trying to read from s3 results')
	s3 = boto3.client('s3')
	obj = s3.get_object(Bucket=S3_BUCKET, Key=KEY)
	logger.info('got object from s3 - setting to array')
	previous_results = json.loads(obj['Body'].read().decode('utf-8'))

	logger.info('read from previous results successfully')
	return previous_results

def write_to_previous_results(results):
 
	logger.info('start trying to write to s3 results')
	s3 = boto3.resource('s3')
	obj = s3.Object(S3_BUCKET, 'previous_results.json')
	obj.put(Body=json.dumps(results))
	
	print('updated previous results successfully')
	return

def do_scrape():

	# login and create a Requests session
	s = eid_login(LOGIN_URL, EID, PASSWORD)
	
	# load previous results 
	previous_results = read_from_previous_results()
	
	# use the session to scrape the events from RecruitTexasMBA site
	results = scrape_events(s)

	# check each result against the previous results and send notification if it is new
	for result in results:
		if result not in previous_results:
			
			# Send the notification to slack 
			messageText = '*New Event*\n{} | {} | {}'.format(result['title'], result['eventType'], result['datetime'])
			
			logger.info('Starting to connect and send message via slack')
			sc = slack.WebClient(SLACK_BOT_TOKEN)
			sc.chat_postMessage(
				channel = SLACK_CHANNEL,
				text = messageText,
				username = botName,
				icon_emoji = botIconEmoji
			)
			print('Sent message to slack')

	# close the Requests session 
	s.close()
	logger.info('closed requested session')
	
	# write the new results to file for use as the previous results next time
	write_to_previous_results(results)
	logger.info('done scraping and messaging')

if __name__ == "__main__":
	do_scrape()