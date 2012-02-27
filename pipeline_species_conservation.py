################################################################################
#
#   MRC FGU Computational Genomics Group
#
#   $Id: pipeline_species_conservation.py 2900 2011-05-24 14:38:00Z david $
#
#   Copyright (C) 2011 David Sims
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
"""
==============================
Species Conservation Pipeline
==============================

:Author: David Sims 
:Release: $Id: pipeline_species_conservation.py 2900 2011-05-24 14:38:00Z david $
:Date: |today|
:Tags: Python

The species conservation pipeline imports lists of genes associated with paricular genomic features 
from multiple species along with a list of 1:1 orthologs from those species.
It then identifies sets of conserved genes that are associated with the same genomic feature.

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline_species_configuration.ini` file. The pipeline looks for a configuration file in several places:

   1. The default configuration in the :term:`code directory`.
   2. A shared configuration file :file:`../pipeline.ini`.
   3. A local configuration :file:`pipeline.ini`.

The order is as above. Thus, a local configuration setting will
override a shared configuration setting and a default configuration
setting.

Configuration files follow the ini format (see the python
`ConfigParser <http://docs.python.org/library/configparser.html>` documentation).
The configuration file is organized by section and the variables are documented within 
the file. In order to get a local configuration file in the current directory, type::

    python <codedir>/pipeline_species_conservation.py config

The sphinxreport report requires a :file:`conf.py` and :file:`sphinxreport.ini` file 
(see :ref:`PipelineDocumenation`). To start with, use the files supplied with the
:ref:`Example` data.


Input
-----

Orthology Data
+++++++++++++++

A single text file containing groups of 1:1 orthologs across all species to be queried.
The format of the file is three column tab-separated:

1. Ortholog_group
2. Species
3. Gene ID

Gene Lists
+++++++++++

A set of text files containing lists of Ensembl gene or transcript ids (one per line) for each species and each condition (tissue).

Gene to Transcript Mapping
++++++++++++++++++++++++++

An annotation database generated by the pipeline_annotation.py for each species.

Requirements
------------

The pipeline requires the information from the following pipelines:

:doc:`pipeline_annotations`

set the configuration variables:
   :py:data:`annotations_database_<species>` 
   :py:data:`annotations_dir_<species>`

Pipline Output
==============

The results of the computation are all stored in an sqlite relational
database :file:`csvdb`.

"""
import sys, tempfile, optparse, shutil, itertools, csv, math, random, re, glob, os, shutil, collections, gzip
import sqlite3
import IOTools
import MAST, GTF, GFF, Bed
import cStringIO
import numpy
import fileinput
import Experiment as E
import logging as L
from ruffus import *

USECLUSTER = True

###################################################
###################################################
###################################################
## Pipeline configuration
###################################################
import Pipeline as P
P.getParameters(  ["%s.ini" % __file__[:-len(".py")],  "../pipeline.ini", "pipeline.ini" ] )
PARAMS = P.PARAMS
#PARAMS_ANNOTATIONS = P.peekParameters( PARAMS["annotations_dir"],"pipeline_annotations.py" )

###################################################################
###################################################################
###################################################################
@files( PARAMS["orthology_groups"], "ortholog_groups.load" )
def loadOrthologousGroups( infile, outfile ):
    '''Load list of orthologous genes into sqlite3 database'''

    header="set_id,species,gene_id"
    statement = '''cat %(infile)s
                   | python %(scriptsdir)s/csv2db.py
                       --header=%(header)s
                       --index=set_id
                       --index=species
                       --index=gene_id
                       --table=ortholog_groups 
                   > %(outfile)s '''
    P.run()

############################################################
@transform( "*.genelist", regex( r"(\S+).genelist"), r"\1.genelist.load" )
def loadGeneLists( infile, outfile ):
    '''Load list of genes associated with feature from each species into sqlite3 database'''

    track = P.snip( os.path.basename( infile), ".genelist" )
    statement = '''cat %(infile)s
                   | python %(scriptsdir)s/csv2db.py
                       --header=gene_id
                       --index=gene_id
                       --table=%(track)s_genelist
                   > %(outfile)s '''
    P.run()

############################################################
@transform( loadGeneLists, suffix( ".genelist.load"), ".genelist.stats" )
def GeneListStats( infile, outfile ):

    track = P.snip( os.path.basename( infile), ".genelist.load" ).replace("-","_")
    species = track[:2]
    anno_base = PARAMS["annotations_dir"]
    species_list = P.asList(PARAMS["species"])
    genome_list = P.asList(PARAMS["genomes"])
    ensembl_version = PARAMS["orthology_ensembl_version"]
    species_lookup = dict(zip(species_list, genome_list))
    species_genome = species_lookup[species]
    species_db = anno_base + species_genome + "/" + PARAMS["database"]

    # Connect to database and attach annotation databases
    dbhandle = sqlite3.connect( PARAMS["database"] )
    cc = dbhandle.cursor()
    statement = '''ATTACH DATABASE '%(species_db)s' as %(species)s''' % locals()
    cc.execute( statement )
    cc.close()

    # Extract data from db
    cc = dbhandle.cursor()
    statement = '''SELECT count(distinct t.gene_id) as genes
                   FROM %(track)s_genelist g, %(species)s.transcript_info t
                   WHERE g.gene_id=t.transcript_id and t.gene_biotype='protein_coding' ''' % locals()
    cc.execute( statement )
    result = cc.fetchall()
    genes_with_feature = str(result[0][0])
    cc.close()
    #print track + " genes_with_feature=" + genes_with_feature + "\n"

    cc = dbhandle.cursor()
    statement = '''SELECT count(distinct gene_id) as genes
                   FROM %(species)s.transcript_info where gene_biotype='protein_coding' ''' % locals()
    cc.execute( statement )
    result = cc.fetchall()
    total_genes = str(result[0][0])
    cc.close()
    #print track + " total_protein_coding_genes =" + total_genes + "\n"

    proportion_with_feature = (float(genes_with_feature)/float(total_genes))*100
    #print track + " proportion_with_feature =" + str(proportion_with_feature) + "%\n"

    cc = dbhandle.cursor()
    statement = '''SELECT count(distinct set_id) as genes
                   FROM ortholog_groups''' % locals()
    cc.execute( statement )
    result = cc.fetchall()
    total_conserved_genes = str(result[0][0])
    cc.close()
    #print "total_conserved_genes =" + total_conserved_genes + "\n"

    proportion_conserved = (float(total_conserved_genes)/float(total_genes))*100
    #print track + " proportion_conserved =" + str(proportion_conserved) + "%\n"

    cc = dbhandle.cursor()
    statement = '''SELECT count(distinct t.gene_id) as genes
                   FROM %(track)s_genelist g, %(species)s.transcript_info t, ortholog_groups o
                   WHERE g.gene_id=t.transcript_id and t.gene_biotype='protein_coding' 
                   AND o.gene_id=t.gene_id''' % locals()
    cc.execute( statement )
    result = cc.fetchall()
    conserved_genes_with_feature = str(result[0][0])
    cc.close()
    #print track + " conserved_genes_with_feature=" + conserved_genes_with_feature + "\n"

    proportion_conserved_with_feature = (float(conserved_genes_with_feature)/float(total_conserved_genes))*100
    #print track + " proportion_conserved_with_feature =" + str(proportion_conserved_with_feature) + "%\n"

    # Write to file
    header = "genes_with_feature\ttotal_genes\ttotal_conserved_genes\tconserved_genes_with_feature\tproportion_with_feature\tproportion_conserved\tproportion_conserved_with_feature"
    outs = open( outfile, "w")
    outs.write( "%s\n" % (header) )
    outs.write( "%s\t%s\t%s\t%s\t%.2f\t%.2f\t%.2f\n" % (genes_with_feature, total_genes, total_conserved_genes, conserved_genes_with_feature, proportion_with_feature, proportion_conserved, proportion_conserved_with_feature) )
    outs.close()

############################################################
@merge( GeneListStats, "genelist_stats.load" )
def loadGeneListStats( infiles, outfile ):
    '''Merge gene list stats into single table and load into SQLite.'''

    tablename = P.toTable( outfile )
    outf = open("genelist_stats.txt","w")

    first = True
    for f in infiles:
        track = P.snip( os.path.basename(f), ".genelist.stats" )
        if not os.path.exists( f ): 
            E.warn( "File %s missing" % f )
            continue
        lines = [ x for x in open( f, "r").readlines() if not x.startswith("#") and x.strip() ]
        if first: outf.write( "%s\t%s" % ("track", lines[0] ) )
        first = False
        outf.write( "%s\t%s" % (track,lines[1] ))
    outf.close()
    tmpfilename = outf.name

    statement = '''cat %(tmpfilename)s
                   | python %(scriptsdir)s/csv2db.py
                      --index=track
                      --table=%(tablename)s 
                   > %(outfile)s '''
    P.run()

############################################################
@merge( loadGeneLists, "genelists_merged.load" )
def mergeGeneLists( infiles, outfile ):
    '''Merge gene lists into single table and load into SQLite.'''

    tablename = P.toTable( outfile )
    anno_base = PARAMS["annotations_dir"]
    species_list = P.asList(PARAMS["species"])
    genome_list = P.asList(PARAMS["genomes"])
    db_name = PARAMS["database"]
    ensembl_version = PARAMS["orthology_ensembl_version"]
    species_lookup = dict(zip(species_list, genome_list))

    # Connect to database and attach annotation databases
    dbhandle = sqlite3.connect( PARAMS["database"] )
    for species in species_lookup.iterkeys():
        species_genome = species_lookup[species]
        species_db = anno_base + species_genome + "/" + db_name
        cc = dbhandle.cursor()
        statement = '''ATTACH DATABASE '%(species_db)s' as %(species)s''' % locals()
        cc.execute( statement )
        cc.close()

    # Build union statement
    pre = "CREATE TABLE %s AS " % tablename
    statement = ""
    for f in infiles:
        track = P.snip( os.path.basename( f), ".genelist.load" ).replace("-","_")
        species = track[:2]
        statement += pre + '''SELECT distinct t.gene_id, t.gene_name
                       FROM %(track)s_genelist g, %(species)s.transcript_info t
                       WHERE g.gene_id=t.transcript_id and t.gene_biotype='protein_coding' ''' % locals()
        pre = " UNION "

    print statement
    cc = dbhandle.cursor()
    cc.execute( "DROP TABLE IF EXISTS %(tablename)s" % locals() )
    cc.execute( statement )
    cc.close()

    statement = "touch %s" % outfile
    P.run()

############################################################
@transform(mergeGeneLists, regex(r"(\S+).load"), "ortholog_groups_with_feature.load") 
def orthologGroupsWithFeature( infile, outfile):
    '''Generate list of conserved genes associated with feature in all species '''

    tablename = "ortholog_groups_with_feature"
    anno_base = PARAMS["annotations_dir"]
    species_list = P.asList(PARAMS["species"])
    genome_list = P.asList(PARAMS["genomes"])
    db_name = PARAMS["database"]
    ensembl_version = PARAMS["orthology_ensembl_version"]
    species_lookup = dict(zip(species_list, genome_list))

    # Connect to database and attach annotation databases
    dbhandle = sqlite3.connect( PARAMS["database"] )
    for species in species_lookup.iterkeys():
        species_genome = species_lookup[species]
        species_db = anno_base + species_genome + "/" + db_name
        cc = dbhandle.cursor()
        statement = '''ATTACH DATABASE '%(species_db)s' as %(species)s''' % locals()
        cc.execute( statement )
        cc.close()

    # Extract data from db
    cc = dbhandle.cursor()
    cc.execute( "DROP TABLE IF EXISTS %(tablename)s" % locals() )
    statement = '''CREATE TABLE %(tablename)s AS 
                   SELECT count(distinct o.species) as species_count, 
                   group_concat(o.gene_id,",") as gene_ids,
                   group_concat(g.gene_name,",") as gene_names,
                   group_concat(o.species,",") as species_list, set_id
                   FROM genelists_merged g, ortholog_groups o
                   WHERE  g.gene_id=o.gene_id
                   GROUP BY set_id ''' % locals()
    cc.execute( statement )
    cc.close()

    statement = "touch %s" % outfile
    P.run()

############################################################
@transform(orthologGroupsWithFeature, suffix(".load"), ".export") 
def exportConservedGeneListPerSpecies( infile, outfile):
    '''Export list of conserved genes associated with feature for each species '''
    
    species_list = P.asList(PARAMS["species"])
    ensembl_version = PARAMS["orthology_ensembl_version"]
    
    # Get gene list from database
    dbhandle = sqlite3.connect( PARAMS["database"] )
    for species in species_list:
        cc = dbhandle.cursor()
        statement = '''SELECT distinct g.gene_id
                       FROM ortholog_groups g, ortholog_groups_with_feature f
                       WHERE f.set_id=g.set_id
                       AND f.species_count=6
                       AND g.species="%(species)s%(ensembl_version)s"''' % locals()
        cc.execute( statement )
        
        # Write to file
        outfilename = species + ".conserved.export"
        outs = open( outfilename, "w")
        for result in cc:
            pre = ""
            for r in result:
              outs.write("%s%s" % (pre, str(r)) )
              pre = "\t"
            outs.write("\n")
        cc.close()
        outs.close()
        
    statement = "touch %s" % outfile
    P.run()

############################################################
@follows( exportConservedGeneListPerSpecies)
@transform( "*.conserved.export", regex(r"(\S+).conserved.export"), r"\1.conserved.bed" )
def exportConservedGeneBed( infile, outfile ):
    '''export bed file for each list of conserved CAPseq genes'''
    species_list = P.asList(PARAMS["species"])
    genome_list = P.asList(PARAMS["genomes"])
    species_lookup = dict(zip(species_list, genome_list))
    species = infile[0:2]
    species_genome = species_lookup[species]
    track = P.snip( os.path.basename(infile),".export")
    
    gtffile = os.path.join( PARAMS["annotations_dir"], species_genome, PARAMS["annotations_gtf"] )
    statement = '''zcat %(gtffile)s | python %(scriptsdir)s/gtf2gtf.py --filter=gene --apply=%(infile)s 
                   | python %(scriptsdir)s/gtf2gtf.py --merge-transcripts --with-utr 
                   | python %(scriptsdir)s/gff2bed.py --is-gtf --name=gene_id --track=feature > %(outfile)s;''' 
    P.run()


############################################################
############################################################
############################################################
## Pipeline organisation
@follows( loadOrthologousGroups, 
          loadGeneLists)
def loadData():
    '''Load all data into database'''
    pass

@follows( GeneListStats, loadGeneListStats)
def stats():
    '''calculate feature conservation stats per gene list'''
    pass

@follows( mergeGeneLists, orthologGroupsWithFeature)
def conservedFeatures():
    '''Find orthologues genes with conserved features '''
    pass
    
@follows( exportConservedGeneListPerSpecies, exportConservedGeneBed)
def export():
    '''Find orthologues genes with conserved features '''
    pass


@follows( loadData, stats,
          conservedFeatures, export)
def full():
    '''Run complete pipeline '''
    pass


############################################################
############################################################
############################################################
## REPORTS
@follows( mkdir( "report" ) )
def build_report():
    '''build report from scratch.'''

    E.info( "starting documentation build process from scratch" )
    P.run_report( clean = True )

@follows( mkdir( "report" ) )
def update_report():
    '''update report.'''

    E.info( "updating documentation" )
    P.run_report( clean = False )

if __name__== "__main__":
    sys.exit( P.main(sys.argv) )

