
# Twisted, the Framework of Your Internet
# Copyright (C) 2001 Matthew W. Lefkowitz
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of version 2.1 of the GNU Lesser General Public
# License as published by the Free Software Foundation.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Authorization & authentication in Twisted.
"""

# System Imports
import md5
import random

# Twisted Imports
from twisted.python import defer, log, failure

class Unauthorized(Exception):
    """An exception that is raised when unauthorized actions are attempted.
    """

class Service:
    """I am a service that internet applications interact with.

    I represent a set of abstractions which users may interact with over a
    specified protocol.

    (See Also: twisted.spread.pb.Service)
    """

    application = None
    serviceType = None
    serviceName = None

    def __init__(self, serviceName, application=None):
        """Create me, attached to the given application.

        Arguments: application, a twisted.internet.app.Application instance.
        """
        self.serviceName = serviceName
        self.perspectives = {}
        self.setApplication(application)

    def cachePerspective(self, perspective):
        """Cache a perspective loaded from an external data source.

        Perspectives that were 'loaded' from memory will not be uncached.
        """
        if self.perspectives.has_key(perspective.perspectiveName):
            return
        self.perspectives[perspective.perspectiveName] = perspective
        perspective._service_cached = 1

    def uncachePerspective(self, perspective):
        """Uncache a perspective loaded from an external data source.

        Perspectives that were 'loaded' from memory will not be uncached.
        """
        if self.perspectives.has_key(perspective.perspectiveName):
            if perspective._service_cached:
                del self.perspectives[perspective.perspectiveName]

    def setApplication(self, application):
        if self.application is not application:
            assert not self.application, "Application already set!"
        if application:
            self.application = application
            application.addService(self)

    def createPerspective(self, name):
        """Create a perspective from self.perspectiveClass and add it to this service.
        """
        p = self.perspectiveClass(name)
        self.perspectives[name] = p
        p.setService(self)
        return p

    def addPerspective(self, perspective):
        """Add a perspective to this Service.
        """
        perspective.setService(self)
        self.perspectives[perspective.getPerspectiveName()] = perspective

    def getPerspectiveNamed(self, name):
        """Return a perspective that represents a user for this service. (DEPRECATED)

        Raises a KeyError if no such user exists.  Override this method to
        provide dynamic instantiation of perspectives.
        """
        return self.perspectives[name]

    def loadPerspective(self, name):
        """Load a perspective from an external data-source.

        If no such data-source exists, return None.  Implement this if you want
        to load your perspectives from somewhere else (e.g. LDAP or a
        database).  It is not recommended to call this directly, since
        getPerspectiveRequest provides management of caching perspectives.
        """
        return defer.fail("No such perspective %s" % name)

    def getPerspectiveRequest(self, name):
        """Return a Deferred which is a request for a perspective on this service.
        """
        try:
            p = self.getPerspectiveNamed(name)
        except KeyError:
            return self.loadPerspective(name)
        else:
            return defer.succeed(p)

    def getServiceName(self):
        """The name of this service.
        """
        return self.serviceName or self.getServiceType()

    def getServiceType(self):
        """Get a string describing the type of this service.
        """
        return self.serviceType or str(self.__class__)

class Perspective:
    """I am an Identity's view onto a service.

    I am the interface through which most 'external' code should
    interact with a service; I represent the actions a user may
    perform upon a service, and the state associated with that
    user for that service.
    """

    _service_cached = 0 # Has my service cached me from a loaded store, or do I live in memory usually?

    def __init__(self, perspectiveName, identityName="Nobody"):
        """Create me.

        I require a name for myself and a reference to the service
        I participate in.  (My identity name will be 'Nobody' by
        default, which will normally not resolve.)
        """
        self.perspectiveName = perspectiveName
        self.identityName = identityName

    def setIdentityName(self, name):
        self.identityName = name

    def setIdentity(self, identity):
        """Determine which identity I connect to.
        """
        self.setIdentityName(identity.name)

    def makeIdentity(self, password):
        """Make an identity from this perspective with a password.

        This is a utility method, which can be used in circumstances
        where the distinction between Perspective and Identity is weak,
        such as single-Service servers.
        """
        ident = Identity(self.perspectiveName, self.service.application)
        self.setIdentityName(self.perspectiveName)
        ident.setPassword(password)
        ident.addKeyForPerspective(self)
        self.service.application.authorizer.addIdentity(ident)
        return ident

    def getPerspectiveName(self):
        """Return the unique name of this perspective.

        This will return a value such that
        self.service.getPerspectiveNamed(value) is self.

        (XXX: That's assuming I have been addPerspective'd to my service.)
        """
        return self.perspectiveName

    def getService(self):
        """Return a service.
        """
        return self.service

    def setService(self, service):
        """Change what service I am a part of.
        """
        self.service = service

    def getIdentityRequest(self):
        """Request my identity.
        """
        return (self.service.application.authorizer.
                getIdentityRequest(self.identityName))

    _attachedCount = 0

    def attached(self, reference, identity):
        """Called when a remote reference is 'attached' to me.

        After being authorized, a remote actor can attach to me
        through its identity.  This call will be made when that
        happens, and the return value of this method will be used
        as the _actual_ perspective to which I am attached.

        Note that the symmetric call, detached, will be made on
        whatever this method returns, _not_ on me.  Therefore,
        by default I return 'self'.
        """
        log.msg('attached [%s]' % str(self.__class__))
        self._attachedCount = self._attachedCount + 1
        if self._attachedCount == 1:
            self.service.cachePerspective(self)
        else:
            log.msg(" (multiple references attached: %s)" % self._attachedCount)
        return self

    def detached(self, reference, identity):
        """Called when a broker is 'detached' from me.

        See 'attached'.

        When a remote actor disconnects (or times out, for example,
        with HTTP), this is called in order to indicate that the
        reference associated with that peer is no longer attached to
        this perspective.
        """
        log.msg('detached [%s]' % str(self.__class__))
        self._attachedCount = self._attachedCount - 1
        if self._attachedCount <= 0:
            self.service.uncachePerspective(self)
            if self._attachedCount < 0:
                log.msg(" (Weird stuff: attached count = %s)" % self._attachedCount)
        else:
            log.msg(" (multiple references attached: %s)" % self._attachedCount)
        return self


# ugh, load order
Service.perspectiveClass = Perspective

def respond(challenge, password):
    """Respond to a challenge.
    This is useful for challenge/response authentication.
    """
    m = md5.new()
    m.update(password)
    hashedPassword = m.digest()
    m = md5.new()
    m.update(hashedPassword)
    m.update(challenge)
    doubleHashedPassword = m.digest()
    return doubleHashedPassword

def challenge():
    """I return some random data.
    """
    crap = ''
    for x in range(random.randrange(15,25)):
        crap = crap + chr(random.randint(65,90))
    crap = md5.new(crap).digest()
    return crap


class Identity:
    """An identity, with different methods for verification.

    An identity represents a user's permissions with a particular
    application.  It is a username, a password, and a collection of
    Perspective/Service name pairs, each of which is a perspective
    that this identity is allowed to access.
    """
    hashedPassword = None

    def __init__(self, name, application):
        """Create an identity.

        I must have a name, and a backreference to the Application that the
        Keys on my keyring make reference to.
        """
        self.name = name
        self.application = application
        self.keyring = {}

    def addKeyForPerspective(self, perspective):
        """Add a key for the given perspective.
        """
        perspectiveName = perspective.getPerspectiveName()
        serviceName = perspective.service.getServiceName()
        self.addKeyByString(serviceName, perspectiveName)

    def addKeyByString(self, serviceName, perspectiveName):
        """Put a key on my keyring.

        This key will give me a token to access to some service in the
        future.
        """
        self.keyring[(serviceName, perspectiveName)] = 1

    def requestPerspectiveForService(self, serviceName):
        """Get the first available perspective for a given service.
        """
        keys = self.keyring.keys()
        keys.sort()
        for serviceN, perspectiveN in keys:
            if serviceN == serviceName:
                return self.requestPerspectiveForKey(serviceName, perspectiveN)
        return defer.fail("No such perspective.")

    def requestPerspectiveForKey(self, serviceName, perspectiveName):
        """Get a perspective request (a Deferred) for the given key.

        If this identity does not have access to the given (serviceName,
        perspectiveName) pair, I will raise KeyError.
        """
        try:
            check = self.keyring[(serviceName, perspectiveName)]
        except KeyError, ke:
            return defer.fail(failure.Failure())
        return self.application.getServiceNamed(serviceName).getPerspectiveRequest(perspectiveName)

    def getAllKeys(self):
        """Returns a list of all services and perspectives this identity can connect to.

        This returns a sequence of keys.
        """
        return self.keyring.keys()

    def removeKey(self, serviceName, perspectiveName):
        """Remove a key from my keyring.

        If this key is not present, raise KeyError.
        """
        del self.keyring[(serviceName, perspectiveName)]

    def setPassword(self, plaintext):
        if plaintext is None:
            self.hashedPassword = None
        else:
            self.hashedPassword = md5.new(plaintext).digest()

    def setAlreadyHashedPassword(self, cyphertext):
        """(legacy) Set a password for this identity, already md5 hashed.
        """
        self.hashedPassword = cyphertext

    def challenge(self):
        """I return some random data.

        This is a method in addition to the module-level function
        because it is anticipated that we will want to change this
        to store salted passwords.
        """
        return challenge()

    def verifyPassword(self, challenge, hashedPassword):
        """Verify a challenge/response password.
        """
        md = md5.new()
        md.update(self.hashedPassword)
        md.update(challenge)
        correct = md.digest()
        result = (hashedPassword == correct)
        return result

    def verifyPlainPassword(self, plaintext):
        """Verify plain text password.

        This is insecure, but necessary to support legacy protocols such
        as IRC, POP3, HTTP, etc.
        """

        md = md5.new()
        md.update(plaintext)
        userPass = md.digest()
        return (userPass == self.hashedPassword)



    # TODO: service discovery through listing of self.keyring.



class Authorizer:
    """An interface to a set of identities.
    """
    def setApplication(self, app):
        """Set the application for this authorizer.
        """
        self.application = app
    def addIdentity(self, identity):
        """Create an identity and make a callback when it has been created.
        """
        raise NotImplementedError()

    def removeIdentity(self, identityName):
        raise NotImplementedError()

    def getIdentityRequest(self, name):
        """Get an identity request, make the given callback when it's received.

        Override this to provide a method for retrieving identities than
        the hash provided by default. The method should return a Deferred.

        Note that this is asynchronous specifically to provide support
        for authenticating users from a database.
        """
        raise NotImplementedError("%s.getIdentityRequest"%str(self.__class__))


class DefaultAuthorizer(Authorizer):
    """I am an authorizer which requires no external dependencies.

    I am implemented as a hash of Identities.
    """

    def __init__(self):
        """Create a hash of identities.
        """
        self.identities = {}

    def addIdentity(self, identity):
        """Add an identity to me.
        """
        if self.identities.has_key(identity.name):
            raise KeyError("Already have an identity by that name.")
        self.identities[identity.name] = identity

    def removeIdentity(self, identityName):
        del self.identities[identityName]

    def getIdentityRequest(self, name):
        """Get a Deferred callback registration object.

        I return a deferred (twisted.python.defer.Deferred) which will
        be called back to when an identity is discovered to be available
        (or errback for unavailable).  It will be returned unarmed, so
        you must arm it yourself.
        """

        req = defer.Deferred()
        if self.identities.has_key(name):
            req.callback(self.identities[name])
        else:
            req.errback("unauthorized")
        return req

