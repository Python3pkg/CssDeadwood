
import sys
import os
import re
import collections
import operator
import logging
import optparse
import json
import pprint

import bs4
import cssutils


# TODO: instead of used vs not used, provide histogram analysis to have better view on hot vs not hot
# TODO: source id/class based elimination: only do search on strings, not logic
# TODO: operation mode to only provide HTML files
# TODO: operation mode to only provide urls


_log = logging.getLogger()



def collect_files(seeds, extensions=None):
    '''
    Collect files from given seeds: files or folders to scan through recursively.

    @param seeds list of root folders or files
    @param extensions optional list of file extensions (lowercase) to filter files
    '''
    files = set()

    # Local function to check extensions (or accept everything)
    if extensions is not None:
        check_extension = lambda path: os.path.splitext(path)[1].lower() in extensions
    else:
        check_extension = lambda path: True

    for seed in seeds:
        if os.path.isfile(seed) and check_extension(seed):
            files.add(seed)
        elif os.path.isdir(seed):
            for (dirpath, dirnames, filenames) in os.walk(seed):
                for filename in filenames:
                    path = os.path.join(dirpath, filename)
                    if check_extension(path):
                        files.add(path)
    return files


def extract_css_selectors(css_file):
    '''
    Extract CSS selectors from a given CSS file.

    @return set of CSS selectors
    '''
    selectors = set()
    # TODO: implement custom/faster selector extraction
    stylesheet = cssutils.parseFile(css_file)
    for rule in stylesheet.cssRules:
        if isinstance(rule, cssutils.css.CSSStyleRule):
            selectors.update([s.selectorText for s in rule.selectorList])
    return selectors


def file_get_contents(file_name):
    with open(file_name) as f:
        contents = f.read()
    return contents


def get_occuring_words(words, content):
    '''
    Return the subset of given words that occur in content.
    '''
    found = set()
    for word in words:
        if re.search(r'\b%s\b' % word, content):
            found.add(word)
    return found





def get_matching_selectors_in_dom(selectors, html_file):
    '''
    Try the given set of CSS selectors on the DOM defined
    by the HTML file and return the subset of selectors that did match.

    @param selectors set of CSS selectors
    @param html_file path to HTML file.

    @return set
    '''
    # TODO: (from http://www.crummy.com/software/BeautifulSoup/bs4/doc/)
    # if CSS selectors are all you need, you might as well use lxml directly, because it's faster.
    with open(html_file) as f:
        soup = bs4.BeautifulSoup(f)

    found_selectors = set()
    for selector in selectors:
        try:
            if len(soup.select(selector)) > 0:
                found_selectors.add(selector)
        except Exception:
            logging.exception('BeautifulSoup select failed on selector %r' % selector)
    return found_selectors


# Some precompiled regexes to extract ids and clasess from CSS selectors.
REGEX_ID = re.compile(r'\#([a-zA-Z0-9]+)')
REGEX_CLASS = re.compile(r'\.([a-zA-Z0-9]+)')


def extract_ids_and_classes_from_selectors(selectors):
    '''
    Extract ids and classes used in the given CSS selectors.

    @return (ids, classes, origins) with:
        ids: set of extracted ids
        classes: set of extracted classes
        origins: mapping of ids (format '#x') and classes (format '.x')
            to list of selectors they were found in.
    '''
    ids = set()
    classes = set()
    origins = collections.defaultdict(lambda: [])
    for selector in selectors:
        for id in REGEX_ID.findall(selector):
            ids.add(id)
            origins['#' + id].append(selector)
        for classs in REGEX_CLASS.findall(selector):
            classes.add(classs)
            origins['.' + classs].append(selector)
    return (ids, classes, origins)



class CssDeadwoodApp(object):
    '''
    CSS Deadwood main() function,
    written OOP-style and chopped up in small methods, for better testability.
    '''


    def _eliminate_selectors_from_dom_matching(self, selectors, html_files):
        # The results struct for tracking intermediate data
        results = {}

        # Start with flagging all selectors as "unused"
        unused_selectors = selectors.copy()
        for html_file in html_files:
            original_total = len(unused_selectors)
            _log.debug('DOM matching %d CSS selectors with DOM from %r' % (original_total, html_file))
            found_selectors = get_matching_selectors_in_dom(unused_selectors, html_file)
            unused_selectors.difference_update(found_selectors)
            _log.info('DOM matching %d CSS selectors: %d matches, %d unmatched with DOM from %r' % (original_total, len(found_selectors), len(unused_selectors), html_file))

        # Return result
        results['unused_selectors'] = sorted(unused_selectors)

        return unused_selectors, results

    def _eliminate_selectors_from_idclass_grepping(self, selectors, src_files):
        '''
        Eliminate selectors by searching for mentioned ids and classes in the given source files.
        '''
        # The results struct for tracking intermediate data
        results = {}

        # Extract ids and classes.
        ids, classes, origins = extract_ids_and_classes_from_selectors(selectors)
        results['ids'] = ids
        results['classes'] = classes
        _log.info('Id/class extraction from %d CSS selectors for source code matching: extracted %d ids, %d classes.' % (len(selectors), len(ids), len(classes)))
        _log.debug('Extracted ids: %r' % ids)
        _log.debug('Extracted classes: %r' % classes)

        # Determine unfindable ids and classes.
        findable_ids = set()
        findable_classes = set()
        unfindable_ids = ids.copy()
        unfindable_classes = classes.copy()
        # Scan through the source files for the remaining ids and classes.
        for src_file in src_files:
            content = file_get_contents(src_file)
            _log.debug('Searching for %d remaining unfindable ids in %s' % (len(unfindable_ids), src_file))
            findable_ids.update(get_occuring_words(unfindable_ids, content))
            unfindable_ids.difference_update(findable_ids)
            _log.debug('Searching for %d remaining unfindable classes in %s' % (len(unfindable_classes), src_file))
            findable_classes.update(get_occuring_words(unfindable_classes, content))
            unfindable_classes.difference_update(findable_classes)
        results['unfindable_ids'] = unfindable_ids
        results['unfindable_classes'] = unfindable_classes

        # Eliminate selectors with findable ids/classes
        used_idclass_selectors = set()
        for id in findable_ids:
            used_idclass_selectors.update(origins['#' + id])
        for classs in findable_classes:
            used_idclass_selectors.update(origins['.' + classs])
        unused_selectors = selectors.difference(used_idclass_selectors)
        _log.info('Id/class based elimination from {total:d} CSS selectors with {src:d} source files: {used:d} possibly used, {unused:d} unused.'.format(
            total=len(selectors),
            src=len(src_files),
            used=len(used_idclass_selectors),
            unused=len(unused_selectors)
        ))

        # return result
        results['unused_selectors'] = sorted(unused_selectors)

        return unused_selectors, results


    def main(self, argv=sys.argv):

        # Parse command line
        option_parser = optparse.OptionParser(usage='%prog [options]')

        option_parser.add_option("--htmlexport", metavar='FILE',
                      action="store", dest="html_export", default=None,
                      help="Export result to a HTML report (requires jinja2 library).")
        option_parser.add_option("--jsonexport", metavar='FILE',
                      action="store", dest="json_export", default=None,
                      help="Export analysis results in JSON format.")

        option_parser.add_option("-v", "--verbose",
                      action="store_const", dest="loglevel", const=logging.DEBUG, default=logging.INFO,
                      help="Be more verbose")

        options, args = option_parser.parse_args(args=argv[1:])

        # Set up logging
        logging.basicConfig(level=options.loglevel)

        # Get CSS, HTML and other source files form given arguments.
        css_files = collect_files(args, extensions=['.css'])
        html_files = collect_files(args, extensions=['.html'])
        # TODO: provide option to set the list of extensions for other source files
        src_files = collect_files(args, extensions=['.php', '.py', '.rb', '.js'])
        _log.info('Working with %d CSS files.' % len(css_files))
        _log.debug('CSS files: %r.' % css_files)
        _log.info('Working with %d HTML files.' % len(html_files))
        _log.debug('HTML files: %r.' % html_files)
        _log.info('Working with %d source files.' % len(src_files))
        _log.debug('Source files: %r.' % src_files)

        # Result object where we will store all analysis data, to be used in reporting/exporting.
        results = {}

        for css_file in css_files:
            _log.info('Analysing CSS selectors from %r' % css_file)
            results[css_file] = {}

            # Extract selectore from CSS source
            selectors = extract_css_selectors(css_file)
            results[css_file]['selectors'] = selectors
            _log.info('Extracted %d CSS selectors from %r.' % (len(selectors), css_file))
            _log.debug('Extracted selectors: %r' % selectors)

            # Start with flagging all selectors as "unused"
            unused_selectors = selectors.copy()

            # Eliminate selectors that match with the DOM trees from the HTML files.
            if html_files:
                unused_selectors, data = self._eliminate_selectors_from_dom_matching(unused_selectors, html_files)
                results[css_file]['dom_matching'] = data

            # Extract ids and classes and scan other source files for these.
            if src_files:
                unused_selectors, data = self._eliminate_selectors_from_idclass_grepping(unused_selectors, src_files)
                results[css_file]['idclass_elimination'] = data

            results[css_file]['unused_selectors'] = sorted(unused_selectors)

        # Report
        for css_file, data in results.items():
            print (css_file + ' ').ljust(80, '-')
            print 'Could not determine usage of the following CSS selectors:'
            print '\n'.join(data['unused_selectors'])

        # TODO: HTML report

        # JSON report
        if options.json_export:
            logging.info('Writing JSON report: %s' % options.json_export)
            with open(options.json_export, 'w') as f:
                json.dump(results, f, indent=1, default=list)





if __name__ == '__main__':
    CssDeadwoodApp().main(sys.argv)