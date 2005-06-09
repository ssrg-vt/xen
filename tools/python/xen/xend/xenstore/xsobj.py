import string
import types

from xen.xend import sxp
from xsnode import XenNode
from xen.util.mac import macToString, macFromString

VALID_KEY_CHARS = string.ascii_letters + string.digits + "_-@"

def hasAttr(obj, attr):
    if isinstance(obj, dict):
        return obj.contains(attr)
    else:
        return hasattr(obj, attr)

def getAttr(obj, attr):
    if isinstance(obj, dict):
        return dict.get(attr)
    else:
        return getattr(obj, attr, None)

def setAttr(obj, attr, val):
    if isinstance(obj, dict):
        dict[attr] = val
    else:
        setattr(obj, attr, val)

class DBConverter:
    """Conversion of values to and from strings in xenstore.
    """

    converters = {}

    def checkType(cls, ty):
        if ty is None or ty in cls.converters:
            return
        raise ValueError("invalid converter type: '%s'" % ty)

    checkType = classmethod(checkType)
    
    def getConverter(cls, ty=None):
        if ty is None:
            ty = "str"
        conv = cls.converters.get(ty)
        if not conv:
            raise ValueError("no converter for type: '%s'" % ty)
        return conv

    getConverter = classmethod(getConverter)

    def convertToDB(cls, val, ty=None):
        return cls.getConverter(ty).toDB(val)

    convertToDB = classmethod(convertToDB)

    def convertFromDB(cls, val, ty=None):
        return cls.getConverter(ty).fromDB(val)

    convertFromDB = classmethod(convertFromDB)

    # Must define in subclass.
    name = None

    def __init__(self):
        self.register()
    
    def register(self):
        if not self.name:
            raise ValueError("invalid converter name: '%s'" % self.name)
        self.converters[self.name] = self

    def toDB(self, val):
        raise NotImplementedError()

    def fromDB(self, val):
        raise NotImplementedError()

class StrConverter(DBConverter):

    name = "str"
    
    def toDB(self, val):
        # Convert True/False to 1/0, otherwise they convert to
        # 'True' and 'False' rather than '1' and '0', even though
        # isinstance(True/False, int) is true.
        if isinstance(val, bool):
            val = int(val)
        return str(val)

    def fromDB(self, data):
        return data

StrConverter()
    
class BoolConverter(DBConverter):

    name = "bool"

    def toDB(self, val):
        return str(int(bool(val)))

    def fromDB(self, data):
        return bool(int(data))

BoolConverter()

class SxprConverter(DBConverter):

    name = "sxpr"
    
    def toDB(self, val):
        return sxp.to_string(val)

    def fromDB(self, data):
        return sxp.from_string(data)
    
SxprConverter()

class IntConverter(DBConverter):

    name = "int"
    
    def toDB(self, val):
        return str(int(val))

    def fromDB(self, data):
        return int(data)
    
IntConverter()
    
class FloatConverter(DBConverter):

    name = "float"
    
    def toDB(self, val):
        return str(float(val))

    def fromDB(self, data):
        return float(data)
    
FloatConverter()
    
class LongConverter(DBConverter):

    name = "long"
    
    def toDB(self, val):
        return str(long(val))

    def fromDB(self, data):
        return long(data)
    
LongConverter()

class MacConverter(DBConverter):

    name = "mac"
    
    def toDB(self, val):
        return macToString(val)

    def fromDB(self, data):
        return macFromString(data)
    
MacConverter()

class DBVar:

    def __init__(self, var, ty=None, path=None):
        DBConverter.checkType(ty)
        if path is None:
            path = var
        self.var = var
        self.ty = ty
        self.path = path
        varpath = filter(bool, self.var.split())
        self.attrpath = varpath[:-1]
        self.attr = varpath[-1]

    def exportToDB(self, db, obj):
        self.setDB(db, self.getObj(obj))

    def importFromDB(self, db, obj):
        self.setObj(obj, self.getDB(db))

    def getObj(self, obj):
        o = obj
        for x in self.attrpath:
            o = getAttr(o, x)
            if o is None:
                return None
        return getAttr(o, self.attr)

    def setObj(self, obj, val):
        o = obj
        for x in self.attrpath:
            o = getAttr(o, x)
        # Don't set obj attr if val is None.
        if val is None and hasAttr(o, self.attr):
            return
        setAttr(o, self.attr, val)

    def getDB(self, db):
        try:
            data = getattr(db, self.path)
        except AttributeError:
            return None
        return DBConverter.convertFromDB(data, ty=self.ty)

    def setDB(self, db, val):
        # Don't set in db if val is None.
        #print 'DBVar>setDB>', self.path, 'val=', val
        if val is None:
            return
        data = DBConverter.convertToDB(val, ty=self.ty)
        #print 'DBVar>setDB>', self.path, 'data=', data
        setattr(db, self.path, data)
        

class DBMap(dict):
    """A persistent map. Extends dict with persistence.
    Set and get values using the usual map syntax:

    m[k],     m.get(k)
    m[k] = v

    Also supports being treated as an object with attributes.
    When 'k' is a legal identifier you may also use

    m.k,     getattr(m, k)
    m.k = v, setattr(m, k)
    k in m,  hasattr(m, k)

    When setting you can pass in a normal value, for example

    m.x = 3

    Getting works too:

    m.x ==> 3

    while m['x'] will return the map for x.

    m['x'].getData() ==> 3
    
    To get values from subdirs use get() to get the subdir first:

    get(m, 'foo').x
    m['foo'].x

    instead of m.foo.x, because m.foo will return the data for field foo,
    not the directory.

    You can assign values into a subdir by passing a map:

    m.foo = {'x': 1, 'y':2 }

    You can also use paths as keys:

    m['foo/x'] = 1

    sets field x in subdir foo.
    
    """

    __db__          = None
    __data__        = None
    __perms__       = None
    __parent__      = None
    __name__        = ""

    __transaction__ = False

    # True if value set since saved (or never saved).
    __dirty__       = True

    def __init__(self, parent=None, name="", db=None):
        if parent is None:
            self.__name__ = name
        else:
            if not isinstance(parent, DBMap):
                raise ValueError("invalid parent")
            self.__parent__ = parent
            self.__name__ = name
            db = self.__parent__.getChildDB(name)
        self.setDB(db)

    def getName(self):
        return self.__name__

    def getPath(self):
        return self.__db__ and self.__db__.relPath()

    def introduceDomain(self, dom, page, evtchn, path=None):
        db = self.__db__
        if path is None:
            path = db.relPath()
        print 'DBMap>introduceDomain>', dom, page, evtchn, path
        try:
            db.introduceDomain(dom, page, evtchn, path)
        except Exception, ex:
            import traceback
            traceback.print_exc()
            print 'DBMap>introduceDomain>', ex
            pass # todo: don't ignore
        
    def releaseDomain(self, dom):
        db = self.__db__
        print 'DBMap>releaseDomain>', dom
        try:
            db.releaseDomain(dom)
        except Exception, ex:
            import traceback
            traceback.print_exc()
            print 'DBMap>releaseDomain>', ex
            pass # todo: don't ignore
        
    def transactionBegin(self):
        # Begin a transaction.
        pass

    def transactionCommit(self):
        # Commit writes to db.
        pass

    def transactionFail(self):
        # Fail a transaction.
        # We have changed values, what do we do?
        pass

    def watch(self, fn):
        pass

    def unwatch(self, watch):
        pass
        
    def checkName(self, k):
        if k == "":
            raise ValueError("invalid key, empty string")
        for c in k:
            if c in VALID_KEY_CHARS: continue
            raise ValueError("invalid key char '%s'" % c)

    def _setData(self, v):
        #print 'DBMap>_setData>', self.getPath(), 'data=', v
        if v != self.__data__:
            self.__dirty__ = True
        self.__data__ = v

    def setData(self, v):
        if isinstance(v, dict):
            for (key, val) in v.items():
                self[key] = val
        else:
            self._setData(v)

    def getData(self):
        return self.__data__

    def _set(self, k, v):
        dict.__setitem__(self, k, v)

    def _get(self, k):
        try:
            return dict.__getitem__(self, k)
        except:
            return None

    def _del(self, k, v):
        try:
            dict.__delitem__(self, k)
        except:
            pass

    def _contains(self, k):
        return dict.__contains__(self, k)
        
    def __setitem__(self, k, v, save=False):
        node = self.addChild(k)
        node.setData(v)
        if save:
            node.saveDB()

    def __getitem__(self, k):
        if self._contains(k):
            v = self._get(k)
        else:
            v = self.readChildDB(k)
            self._set(k, v)
        return v

    def __delitem__(self, k):
        self._del(k)
        self.deleteChildDB(k)

    def __repr__(self):
        if len(self):
            return dict.__repr__(self)
        else:
            return repr(self.__data__)

    def __setattr__(self, k, v):
        if k.startswith("__"):
            object.__setattr__(self, k, v)
        else:
            self.__setitem__(k, v, save=True)
        return v
    
    def __getattr__(self, k):
        if k.startswith("__"):
            v = object.__getattr__(self, k)
        else:
            try:
                v = self.__getitem__(k).getData()
            except LookupError, ex:
                raise AttributeError(ex.args)
        return v

    def __delattr__(self, k):
        return self.__delitem__(k)

    def delete(self):
        dict.clear(self)
        self.__data__ = None
        if self.__db__:
            self.__db__.delete()

    def clear(self):
        dict.clear(self)
        if self.__db__:
            self.__db__.deleteChildren()

    def getChild(self, k):
        return self._get(k)

    def getChildDB(self, k):
        self.checkName(k)
        return self.__db__ and self.__db__.getChild(k)

    def deleteChildDB(self, k):
        if self.__db__:
            self.__db__.deleteChild(k)

    def _addChild(self, k):
        kid = self._get(k)
        if kid is None:
            kid = DBMap(parent=self, name=k, db=self.getChildDB(k))
            self._set(k, kid)
        return kid

    def addChild(self, path):
        l = path.split("/")
        n = self
        for x in l:
            if x == "": continue
            n = n._addChild(x)
        return n

    def setDB(self, db):
        if (db is not None) and not isinstance(db, XenNode):
            raise ValueError("invalid db")
        self.__db__ = db
        for (k, v) in self.items():
            if v is None: continue
            if isinstance(v, DBMap):
                v._setDB(self.addChild(k), restore)

    def readDB(self):
        if self.__db__ is None:
            return
        self.__data__ = self.__db__.getData()
        for k in self.__db__.ls():
            n = self.addChild(k)
            n.readDB()
        self.__dirty__ = False

    def readChildDB(self, k):
        if self.__db__ and (k in self.__db__.ls()):
            n = self.addChild(k)
            n.readDB()
        raise LookupError("invalid key '%s'" % k)

    def saveDB(self, sync=False, save=False):
        """Save unsaved data to db.
        If save or sync is true, saves whether dirty or not.
        If sync is true, removes db entries not in the map.
        """
        
        if self.__db__ is None:
            #print 'DBMap>saveDB>',self.getPath(), 'no db'
            return
        # Write data.
        #print 'DBMap>saveDB>', self.getPath(), 'dirty=', self.__dirty__, 'data=', self.__data__
        if ((self.__data__ is not None)
            and (sync or save or self.__dirty__)):
            self.__db__.setData(self.__data__)
            self.__dirty__ = False
        else:
            #print 'DBMap>saveDB>', self.getPath(), 'not written'
            pass
        # Write children.
        for (name, node) in self.items():
            if not isinstance(node, DBMap): continue
            node.saveDB(sync=sync, save=save)
        # Remove db nodes not in children.
        if sync:
            for name in self.__db__.ls():
                if name not in self:
                    self.__db__.delete(name)

    def importFromDB(self, obj, fields):
        """Set fields in obj from db fields.
        """
        for f in fields:
            f.importFromDB(self, obj)

    def exportToDB(self, obj, fields, save=False, sync=False):
        """Set fields in db from obj fields.
        """
        for f in fields:
            f.exportToDB(self, obj)
        self.saveDB(save=save, sync=sync)
