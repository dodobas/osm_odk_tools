from urlparse import urljoin
import xml.etree.ElementTree as ET
# import lxml.etree as ET
import requests
import csv
import os

import logging
logger = logging.getLogger(__name__)


class ODKAggregateExport(object):
    """
    ODK Aggregate service exporter
    """
    def __init__(self, theODKAggServer, theFormName, theStorageFolder='/tmp'):
        self.ODK_AGGSERVER = theODKAggServer
        self.formName = theFormName
        self.storageFolder = theStorageFolder

        self.submissions = []

        self.get_form_list()

        if self.formlist:
            self.get_form()

        if self.formid:
            self.get_form_submissions()

        if self.submissionIDs:
            self.get_submissions()

        if len(self.submissions) != 0:
            self.save_data()

    def get_form_list(self):
        myURL = urljoin(self.ODK_AGGSERVER, 'formList')
        r = requests.get(myURL)
        if r.status_code == 200:
            logger.debug('formList response %s', r.content)
            root = ET.fromstring(r.content)

            if root.tag == 'forms':
                self.formlist = list(root)
            else:
                self.formlist = None
        else:
            logger.error(
                'formList request %s response %i', myURL, r.status_code)
            self.formlist = None

    def get_form(self):
        myForm = [
            form for form in self.formlist if form.text == self.formName
        ]
        if len(myForm) == 1:
            self.form = myForm[0]
            self.formURL = self.form.attrib.get('url')
            r = requests.get(self.formURL)
            if r.status_code == 200:
                logger.debug('formXml response %s', r.content)
                root = ET.fromstring(r.content)
                # data element
                myDataElement = root.find(
                    './/{http://www.w3.org/2002/xforms}data')

                self.formid = myDataElement.attrib.get('id')
                self.formattrs = [
                    elm.tag.split('}')[1]
                    for elm in myDataElement.findall('./')]

                logger.info('formId = %s', self.formid)
                logger.info('formAttrs = %s', self.formattrs)
            else:
                logger.error(
                    'formXML request %s response %i',
                    self.formURL, r.status_code)
                self.formid = None

    def get_form_submissions(self):
        myURL = urljoin(
            self.ODK_AGGSERVER, 'view/submissionList')
        r = requests.get(myURL, params={
            'cursor': '', 'formId': self.formid, 'numEntries': 100}
        )
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            self.submissionIDs = (elem.text for elem in root.findall(
                './/{http://opendatakit.org/submissions}id'))
        else:
            self.submissionIDs = None

    def get_submissions(self):
        myURL = urljoin(self.ODK_AGGSERVER, 'view/downloadSubmission')
        for submission in self.submissionIDs:
            r = requests.get(myURL, params={
                'formId': '{0}[@version=null+and+@uiVersion=null]/data'
                '[@key={1}]'.format(self.formid, submission)
            })
            root = ET.fromstring(r.content)
            myNamespace = '{http://opendatakit.org/submissions}'
            myDataElem = root.find('.//{0}data[@id]'.format(myNamespace))
            # process form data
            myFormData = {
                elem.tag.split('}')[1]: elem.text
                for elem in myDataElem.getchildren()
            }
            # process form media
            myMediaNodes = root.findall('.//{0}mediaFile'.format(myNamespace))
            myFormMedia = [
                {el.tag.split('}')[1]: el.text for el in elem}
                for elem in myMediaNodes
            ]
            self.submissions.append((myFormData, myFormMedia))

    def save_data(self):
        myDirectory = os.path.join(self.storageFolder, self.formid)
        if not(os.path.exists(myDirectory)):
            try:
                os.makedirs(myDirectory)
                logger.debug('Created submission directory: %s', myDirectory)
            except OSError, e:
                logger.error(
                    'Error during submission directory creation: %s (%s)',
                    myDirectory, str(e)
                )

        myFilename = 'data.csv'
        myPath = os.path.join(myDirectory, myFilename)
        with open(myPath, 'w') as csvfile:
            myWriter = csv.DictWriter(csvfile, fieldnames=self.formattrs)
            myWriter.writeheader()
            myWriter.writerows((subm[0] for subm in self.submissions))
        logger.debug('Wrote submissions data as csv!')

        myMediaDirectory = os.path.join(myDirectory, 'media')

        if not(os.path.exists(myMediaDirectory)):
            try:
                os.makedirs(myMediaDirectory)
                logger.debug('Created submission media directory: %s', myDirectory)
            except OSError, e:
                logger.error(
                    'Error during submission media directory creation: %s (%s)',
                    myDirectory, str(e)
                )

        for submission in (subm[1] for subm in self.submissions):
            for mediaFile in submission:
                myMediaFilename = os.path.join(
                    myMediaDirectory, mediaFile.get('filename'))
                myMediaDownloadURL = mediaFile.get('downloadUrl')
                r = requests.get(myMediaDownloadURL)
                if r.status_code == 200:
                    with open(myMediaFilename, 'wb') as mediaData:
                        mediaData.write(r.content)
                else:
                    logger.error(
                        'mediaDownload request %s response %i',
                        myMediaDownloadURL, r.status_code)

if __name__ == '__main__':
    # setup logging when running this module
    logging.basicConfig(level=logging.INFO)
    myODKAGGServer = ODKAggregateExport(
        'http://suhozid.geof.unizg.hr/ODKAggregate/', 'Surveillance')
