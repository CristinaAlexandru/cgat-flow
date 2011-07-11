"""collect scripts from a directory and create documentation
stubs.

Files starting with lower case characters are put into
the directory :file:`scripts`, while those starting with
upper case characters are put into :file:`modules`.
"""

TEMPLATE_RST='''
.. automodule:: %(name)s
   :members:
   :inherited-members:
   :show-inheritance:
'''

import sys, glob, re, os

import Experiment as E

if __name__ == "__main__":

    E.Start()

    dirs = (os.path.abspath(".."), )

    ncreated, nskipped = 0, 0
    for dir in dirs:

        files = glob.glob( os.path.join( os.path.abspath( dir ), "*.py") )

        for filename in files:
            dirname, name = os.path.split( filename )
            prefix = name[:-3]

            if os.path.exists( os.path.join( dirname, "_%s.pyx" % prefix )):
                E.warn( "ignoring pyximport file _%s.pyx" % prefix )
                continue

            if prefix[0].isupper(): 
                dest = "modules"
            else:
                dest = "scripts"

            filename = os.path.join( os.path.abspath(dest), "%s.rst" % prefix )
            if os.path.exists( filename ):
                nskipped += 1
                continue

            outfile = open( filename, "w" )
            outfile.write( TEMPLATE_RST % locals() )
            outfile.close()

            ncreated += 1

    E.info( "ncreated=%i, nskipped=%i" % (ncreated, nskipped ) )
    
    E.Stop()
