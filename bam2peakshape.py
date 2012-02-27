################################################################################
#
#   MRC FGU Computational Genomics Group
#
#   $Id: script_template.py 2871 2010-03-03 10:20:44Z andreas $
#
#   Copyright (C) 2009 Andreas Heger
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#################################################################################
'''
bam2peakshape.py - compute peak shape features from a bam-file
==============================================================

:Author: Andreas Heger
:Release: $Id$
:Date: |today|
:Tags: Python

Purpose
-------

This script takes a :term:`bed` formatted file with regions of interest,
for example binding intervals from a ChIP-Seq experiment. Using a collection
of aligned reads is a :term:`bam` formatted file, the script outputs a collection
of features describing the peak shape.

Among the features output are:

peak-height - number of reads at peak
peak-median - median coverage compared to peak height within interval
interval-width - width of interval
peak-max-width - width of peak. Distance to peak of furthest half-height position.
peak-min-width - width of peak. Distance to peak of closest half-height position.

histogram - a histogram of read depth within the interval. 

.. todo::
   
   paired-endedness is not fully implemented.

Usage
-----

Example::

   python script_template.py --help

Type::

   python script_template.py --help

for command line help.

Documentation
-------------

For read counts to be correct the NH flag to be set correctly.

Code
----

'''

import os, sys, re, optparse, collections
import Experiment as E
import IOTools
import pysam
import Bed
import numpy

import pyximport
pyximport.install(build_in_temp=False)
import _bam2peakshape

def main( argv = None ):
    """script main.

    parses command line options in sys.argv, unless *argv* is given.
    """

    if not argv: argv = sys.argv

    # setup command line parser
    parser = optparse.OptionParser( version = "%prog version: $Id: script_template.py 2871 2010-03-03 10:20:44Z andreas $", 
                                    usage = globals()["__doc__"] )

    # parser.add_option( "-c", "--centre", dest="centre", action="store_true",
    #                    help = "center interval on maximum coverage interval [%default]" )

    parser.add_option( "-w", "--window-size", dest="window_size", type = "int",
                       help = "normalize all intervals to the same size. " 
                              "[%default]" )

    parser.add_option( "-a", "--bamfile", dest="bamfiles", type = "string", action = "append",
                       help = "BAM files to use"
                              "[%default]" )                              

    parser.add_option( "-e", "--bedfile", dest="bedfile", type = "string",
                       help = "BED file to use"
                              "[%default]" )  

    parser.add_option( "-b", "--bin-size", dest="bin_size", type = "int",
                       help = "bin-size for histogram of read depth. "
                              "[%default]" )

    parser.add_option( "-s", "--sort", dest="sort", type = "choice", action = "append",
                       choices = ("peak-height", "peak-width", "unsorted" ),
                       help = "output sort order for matrices. "
                              "[%default]" )

    parser.add_option( "-i", "--shift", dest="shift", type = "int", action = "append",
                       help = "shift for reads (1 per bam file in order). "
                              "[%default]" )

    parser.set_defaults(
        remove_rna = False,
        ignore_pairs = False,
        input_reads = 0,
        force_output = False,
        bin_size = 10,
        shift = [],
        bamfiles = [],
        bedfile = "",
        window_size = 0,
        sort = []
        )

    ## add common options (-h/--help, ...) and parse command line 
    (options, args) = E.Start( parser, argv = argv, add_output_options = True )

    if len(args) == 2:
        bamfile, bed = args
        options.bamfiles.append(bamfile)
        options.bedfile = bed

    if len(options.bamfiles) > 0:
        if options.bamfiles[0].endswith( ".bam" ):
            bamfiles = [ pysam.Samfile( x, "rb" ) for x in options.bamfiles ]
        else:
            raise NotImplementedError( "can't determine file type for %s" % bamfile )
                    
    # Write headers
    options.stdout.write( "\t".join( ("contig", 
                                      "start",
                                      "end",
                                      "name",
                                      "\t".join(_bam2peakshape.PeakShapeResult._fields) ) ) + "\n" )
    
    if options.window_size:
        # bins are centered at peak-center and then stretching outwards.
        bins = numpy.arange( -options.window_size + options.bin_size // 2, 
                              +options.window_size, 
                              options.bin_size )        
        
    result =[]
    for bed in Bed.iterator( IOTools.openFile( options.bedfile ) ):
        # print "%s:%i-%i" % (bed.contig, bed.start, bed.end)
        features = _bam2peakshape.count( bamfiles, bed.contig, bed.start, bed.end, 
                                         shift = options.shift,
                                         bins = bins )
        result.append( (features, bed) )

    # center bins
    out_bins = bins[:-1] + options.bin_size

    def writeMatrix( result, sort ):

        outfile_matrix = E.openOutputFile( "matrix_%s.gz" % re.sub("-", "_", sort ) )            
        outfile_matrix.write( "name\t%s\n" % "\t".join( map(str, out_bins )))
        
        for features, bed in result:
            options.stdout.write( "%s\t%i\t%i\t%s\t" % (bed.contig, bed.start, bed.end, bed.name ) )
            options.stdout.write( "\t".join( map(str, features[:-2] ) ) )
            bins, counts = features[-2], features[-1]
            options.stdout.write( "\t%s" % ",".join( map(str, bins )))
            options.stdout.write( "\t%s" % ",".join( map(str, counts)))
            options.stdout.write( "\n" )

            outfile_matrix.write( "%s\t%s\n" % (bed.name, "\t".join(map(str,counts))) )

        outfile_matrix.close()

    # output sorted matrices
    if not options.sort: writeMatrix( result, "unsorted" )
    for sort in options.sort: 
        if sort == "peak-height":
            result.sort( key = lambda x: x[0].peak_height )
            
        elif sort == "peak-width":
            result.sort( key = lambda x: x[0].peak_width )

        writeMatrix( result, sort )

    ## write footer and output benchmark information.
    E.Stop()

if __name__ == "__main__":
    sys.exit( main( sys.argv) )

    
