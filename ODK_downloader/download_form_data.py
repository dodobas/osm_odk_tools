from urlparse import urljoin
import xml.etree.ElementTree as ET
# import lxml.etree as ET
import requests
import csv
import os
import itertools
import cStringIO
import codecs
import logging
logger = logging.getLogger(__name__)


class UnicodeDictWriter:
    def __init__(self, f, fieldnames, restval="", extrasaction="raise",
                 dialect="excel", *args, **kwds):
        self.fieldnames = fieldnames    # list of keys for the dict
        self.restval = restval          # for writing short dicts
        if extrasaction.lower() not in ("raise", "ignore"):
            raise ValueError(
                "extrasaction (%s) must be 'raise' or 'ignore'" %
                extrasaction)
        self.extrasaction = extrasaction
        self.writer = csv.writer(f, dialect, *args, **kwds)

    def writeheader(self):
        header = dict(zip(self.fieldnames, self.fieldnames))
        self.writerow(header)

    def _dict_to_list(self, rowdict):
        if self.extrasaction == "raise":
            wrong_fields = [k for k in rowdict if k not in self.fieldnames]
            if wrong_fields:
                raise ValueError("dict contains fields not in fieldnames: " +
                                 ", ".join(wrong_fields))
        return [
            rowdict.get(key, self.restval).encode("utf-8")
            for key in self.fieldnames]

    def writerow(self, rowdict):
        return self.writer.writerow(self._dict_to_list(rowdict))

    def writerows(self, rowdicts):
        rows = []
        for rowdict in rowdicts:
            rows.append(self._dict_to_list(rowdict))
        return self.writer.writerows(rows)


def union(*dicts):
    """
    Create a union of many dictionaries
    """
    return dict(itertools.chain(*map(lambda dct: list(dct.items()), dicts)))


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
                logger.info('Server form list: %s', [
                    form.text for form in self.formlist])
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

    def parse_data(self, elem):
        myElem = list(elem)
        if len(myElem) > 1:
            return {myElem[0].tag.split('}')[1]: '*$*'.join([
                '*|*'.join(
                    ['||'.join(
                        [innerElem.tag.split('}')[1], innerElem.text or ''])
                        for innerElem in groupElem.getchildren()])
                for groupElem in myElem])}
        else:
            if len(myElem[0].getchildren()) > 0:
                return {myElem[0].tag.split('}')[1]: '*|*'.join(
                    ['||'.join(
                        [innerElem.tag.split('}')[1], innerElem.text or ''])
                        for innerElem in myElem[0].getchildren()])}
            else:
                return {myElem[0].tag.split('}')[1]: myElem[0].text or u''}

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
            myData = itertools.groupby(
                myDataElem.getchildren(), key=lambda x: x.tag.split('}')[1])
            # process form data
            myFormData = union(*[
                self.parse_data(elem) for _, elem in myData
            ])
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
        with open(myPath, 'wb') as csvfile:
            myWriter = UnicodeDictWriter(csvfile, fieldnames=self.formattrs)
            myWriter.writeheader()
            #import pdb; pdb.set_trace()
            myWriter.writerows((subm[0] for subm in self.submissions))

        logger.debug('Wrote submissions data as csv!')

        myMediaDirectory = os.path.join(myDirectory, 'media')

        if not(os.path.exists(myMediaDirectory)):
            try:
                os.makedirs(myMediaDirectory)
                logger.debug(
                    'Created submission media directory: %s', myDirectory)
            except OSError, e:
                logger.error(
                    'Error creating submission media directory: %s (%s)',
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
        'http://suhozid.geof.unizg.hr/ODKAggregate/',
        'SUHOZID-2013.03-fanatic')
