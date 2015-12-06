import os.path
import json
import tornado.web
import tornado.httpserver
import tornado.ioloop
import tornado.options

from model import *
from helper import *
from feedfetcher import *
from xml.etree import ElementTree
import config
import subprocess

from tornado.ioloop import PeriodicCallback

from tornado.options import define, options
define('port', default=9999, help='run on the given port', type=int)

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r'/', MainHandler),
            (r'/feed/(\d+)', FeedHandler),
            (r'/item/(\d+)', ItemHandler),
            (r'/star', StarHandler),
            (r'/login', LoginHandler),
            (r'/logout', LogoutHandler),

            (r'/itemstatus', ItemStatusHandler),
            (r'/addfeed', AddFeedHandler),
            (r'/importopml', ImportOpmlHandler),
            (r'/update', UpdateHandler),
        ]
        settings = dict(
            template_path=os.path.join(os.path.dirname(__file__),
                                        'templates'),
            static_path=os.path.join(os.path.dirname(__file__),
                                        'templates/static'),
            ui_modules={'Sidebar': SidebarModule,
                        'Toolbar': ToolbarModule,
                        },
            cookie_secret='1234',
            debug=True
        )
        tornado.web.Application.__init__(self, handlers, **settings)
        self.db = scoped_session(sessionmaker(bind=engine))


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        user_id = self.get_secure_cookie('uid')
        if user_id:
            try:
                user = self.db.query(Admin).filter_by(userid=user_id).one()
            except NoResultFound:
                return
            else:
                return user

    @property
    def db(self):
        return self.application.db

    @property
    def uri_query(self):
        return QueryParser(self.request.query)


class SidebarModule(tornado.web.UIModule):
    def render(self, current_feed):
        all_feeds = self.handler.db.query(Feed).order_by(Feed.feedid)

        return self.render_string('sidebar.html',
                                    all_feeds=all_feeds,
                                    current_feed=current_feed,
                                    admin_user=self.current_user)

class ToolbarModule(tornado.web.UIModule):
    def render(self, admin_user, viewmode, subpage, pagination):
        return self.render_string('toolbar.html',
                                    admin_user=admin_user,
                                    viewmode=viewmode,
                                    subpage=subpage,
                                    pagination=pagination)


class LoginHandler(BaseHandler):
    def post(self):
        if self.current_user:
            return

        username = self.get_argument('username')
        password = self.get_argument('password')

        enc_password = encrypt_password(username, password)

        result = self.db.query(Admin).\
                filter_by(username=username).\
                filter_by(password=enc_password)

        if result.count():
            #self.set_secure_cookie('uid', str(result.one().userid))
            self.set_secure_cookie('uid', str(1))
        self.redirect('/')


class LogoutHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.clear_cookie('uid')
        self.redirect('/')


class MainHandler(BaseHandler):
    def get(self):
        if self.uri_query.mode == 'all':
            all_items_number = self.db.query(Item).count()
        elif self.uri_query.mode == 'normal':
            all_items_number = self.db.query(Item).\
                                filter_by(readed=False).count()
        per_page = config.Index_per_page
        page_number = self.uri_query.more
        pagination = Pagination(page_number, all_items_number, per_page)

        if self.uri_query.mode == 'all':
            newest_items = self.db.query(Item).\
                            order_by(Item.pubdate.desc()).\
                            offset(pagination.start_point).\
                            limit(pagination.per_page)
        elif self.uri_query.mode == 'normal':
            newest_items = self.db.query(Item).\
                            filter_by(readed=False).\
                            order_by(Item.pubdate.desc()).\
                            offset(pagination.start_point).\
                            limit(pagination.per_page)

        current_feed = 0

        self.render('list.html',
                    newest_items=newest_items,
                    pagination=pagination,
                    subpage=None,
                    current_feed=current_feed,
                    viewmode=self.uri_query.mode,
                    admin_user=self.current_user)


class FeedHandler(BaseHandler):
    def get(self, feedid):
        if self.uri_query.mode == 'all':
            all_items_number = self.db.query(Item).\
                                filter_by(feedid=feedid).count()
        elif self.uri_query.mode == 'normal':
            all_items_number = self.db.query(Item).\
                                filter_by(readed=False).\
                                filter_by(feedid=feedid).count()
        per_page = config.Index_per_page
        page_number = self.uri_query.more
        pagination = Pagination(page_number, all_items_number, per_page)

        if self.uri_query.mode == 'all':
            items = self.db.query(Item).filter_by(feedid=feedid).\
                    order_by(Item.pubdate.desc()).\
                    offset(pagination.start_point).\
                    limit(pagination.per_page)
        elif self.uri_query.mode == 'normal':
            items = self.db.query(Item).filter_by(feedid=feedid).\
                    filter_by(readed=False).\
                    order_by(Item.pubdate.desc()).\
                    offset(pagination.start_point).\
                    limit(pagination.per_page)

        feed_info = self.db.query(Feed).filter_by(feedid=feedid).one()
        current_feed = feed_info.feedid
        subpage = dict(type='feed',
                        id=feed_info.feedid,
                        name=feed_info.feedname)

        if not items.count():
            raise tornado.web.HTTPError(404)

        self.render('list.html',
                    newest_items=items,
                    pagination=pagination,
                    subpage=subpage,
                    current_feed=current_feed,
                    viewmode=self.uri_query.mode,
                    admin_user=self.current_user)


class ItemHandler(BaseHandler):
    def get(self, itemid):
        item = self.db.query(Item).filter_by(itemid=itemid).one()
        feed_info = item.feed
        subpage = dict(type='feed',
                        id=feed_info.feedid,
                        name=feed_info.feedname)

        current_feed = feed_info.feedid

        if self.current_user:
            if not item.readed:
                item.readed = True
                item.feed.itemunread -= 1
                self.db.add(item)
                self.db.commit()

        self.render('article.html',
                    article=item,
                    subpage=subpage,
                    pagination=None,
                    viewmode=None,
                    current_feed=current_feed,
                    admin_user=self.current_user)


class StarHandler(BaseHandler):
    def get(self):
        newest_items = self.db.query(Item).\
                filter_by(star=1).\
                order_by(Item.pubdate.desc())
        
        all_items_number = newest_items.count()
        per_page = config.Index_per_page
        page_number = self.uri_query.more
        pagination = Pagination(page_number, all_items_number, per_page)

        subpage = dict(type='star', url='/star', name='Star')

        self.render('list.html',
                    newest_items=newest_items,
                    pagination=pagination,
                    current_feed=0,
                    subpage=subpage,
                    viewmode=self.uri_query.mode,
                    admin_user=self.current_user)


class ItemStatusHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        itemid = self.get_argument('itemid')
        try:
            read = self.get_argument('read')
        except:
            try:
                star = self.get_argument('star')
            except:
                pass
            else:
                result = 'star'
        else:
            result = 'read'

        item = self.db.query(Item).filter_by(itemid=itemid).one()
        if result == 'star':
            item.star = not item.star
        elif result == 'read':
            item.readed = not item.readed
            if item.readed:
                item.feed.itemunread -= 1
            else:
                item.feed.itemunread += 1

        self.db.add(item)
        self.db.commit()

        self.redirect(self.request.headers.get('referer', '/'))


class AddFeedHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        new_feedurl = self.get_argument('newfeed')

        result = self.db.query(Feed).filter_by(feedurl=new_feedurl)
        if result.count():
            pass
        else:
            dumper = Fetcher(new_feedurl)
            dumper.parse_feed()
            dumper.parse_items()
            dumper.save_to_db()

        self.redirect('/')
 
class ImportOpmlHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        #new_feedurl = self.get_argument('importopml')
        with open("feedly.opml", 'rt') as f:
            tree = ElementTree.parse(f)
            
            for node in tree.findall('.//outline'):
                title = node.attrib.get('title')
                xmlurl = node.attrib.get('xmlUrl')
                htmlurl = node.attrib.get('htmlUrl')
        
                if title and xmlurl and htmlurl:
                    print title, xmlurl, htmlurl
                    #urls.append(url)
                    #return urls
                    dumper = Fetcher(xmlurl)
                    dumper.fast_fill(title, xmlurl, htmlurl)
                    #dumper.parse_feed()
                    #dumper.parse_items()
                    dumper.save_to_db()
                    """
                    result = self.db.query(Feed).filter_by(feedurl=xmlurl)
                    if result.count():
                        pass
                    else:
                        dumper = Fetcher(xmlurl)
                        dumper.parse_feed()
                        dumper.parse_items()
                        dumper.save_to_db()
                    """
        self.redirect('/')
 
class UpdateHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        #new_feedurl = self.get_argument('newfeed')
        
        subprocess.call(['python', 'feedfetcher.py'])
        """
        result = self.db.query(Feed).filter_by(feedurl=new_feedurl)
        if result.count():
            pass
        else:
            dumper = Fetcher(new_feedurl)
            dumper.parse_feed()
            dumper.parse_items()
            dumper.save_to_db()

        self.redirect('/')
        """ 

if __name__ == '__main__':
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)

    # Add schedule update check, better do this in cron 
    #schedule_check = PeriodicCallback(feedfetcher.check_new,
    #                                config.Fetch_per_hours*3600*1000)
    #schedule_check.start()
    print "server is ready, enter IO loop"
    tornado.ioloop.IOLoop.instance().start()
