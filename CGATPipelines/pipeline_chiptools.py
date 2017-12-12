"""
====================================================================
pipeline_chiptools - homer and deeptools implimentation
====================================================================


Overview
========

The aim of this pipeline is to create peaklists in :term:`bed` files from
aligned reads in :term:`bam` files that can then be taken on to downstream
analysis (e.g., quantification of peaks etc.). The pipeline wraps up
homer and deeptools software for ATAC and ChIP-seq analysis.

This pipeline also performs motif analysis and basic QC analysis
(i.e., basic tag information, read length distribution, clonal tag distribution
(clonal read depth), autocorrelation analysis (distribution of distances
between adjacent reads in the genome) and sequence bias analysis).

Principal targets
-----------------

standard
    perform all workflows minus the motif generation

full
    run all chiptools tasks

Functionality
+=============

- Takes paired-end or single end :term:`Bam` files you want to call peaks on
  (e.g. ChIP-Seq or ATAC-Seq samples and their appropriate 'input' controls).
- Creates Tag directories for ChIP and Input :term:'Bam' files
- Runs homer peakcaller (findPeaks)
- Produces peak lists in bed files to take forward for downstream analysis.
- Performs motif discovery analysis
- Performs peak annotation
- Finds differential and common  peaks between replicates (reproducibility)
  and between samples (differential binding)

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general
information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.ini` file.
CGATReport report requires a :file:`conf.py` and optionally a
:file:`cgatreport.ini` file (see :ref:`PipelineReporting`).

Default configuration files can be generated by executing:

   python <srcdir>/pipeline_chiptools.py config

Input files
-----------

The pipeline requres a sample `bam` file and an optional input `bam` file to
perform background evaluation. The bam file should be indexed.

The pipeline requires a pipeline.ini file that needs to be popeluated with information
that will allow the pipeline to execute correctly. This can be generated using the
command:

       cgatflow chiptools config

A folder called design.tsv, which is a tab seperated file also needs to be supplied
for running both homer and deeptools. The file needs to be specified with the following
headers

SampleID Tissue Factor Condition Treatment Replicate bamReads ControlID bamControl

For running deeptools you also need to specify a bed file of interested regions. Please
see the pipeline.ini under the deep options for more details.

Code
====

"""
from ruffus import *
import sys
import os
import CGAT.Experiment as E
import CGATPipelines.Pipeline as P
import CGATPipelines.PipelinePeakcalling as PipelinePeakcalling
import CGAT.BamTools as Bamtools
import CGAT.IOTools as IOTools
import matplotlib.pyplot as plt
import pandas as pd

# load options from the config file
PARAMS = P.getParameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"])


#######################################################################
# Check for design file & Match ChIP/ATAC-Seq Bams with Inputs ########
#######################################################################


# This section checks for the design table and generates:
# 1. A dictionary, inputD, linking each input file and each of the various
#    IDR subfiles to the appropriate input, as specified in the design table
# 2. A pandas dataframe, df, containing the information from the
#    design table.
# 3. INPUTBAMS: a list of control (input) bam files to use as background for
#    peakcalling.
# 4. CHIPBAMS: a list of experimental bam files on which to call peaks on.

# if design table is missing the input and chip bams  to empty list. This gets
# round the import tests


if os.path.exists("design.tsv"):
    df, inputD = PipelinePeakcalling.readDesignTable("design.tsv", "none")

    INPUTBAMS = list(df['bamControl'].values)
    CHIPBAMS = list(df['bamReads'].values)
    TOTALBAMS = INPUTBAMS + CHIPBAMS

    # I have defined a dict of the samples to I can parse the correct
    # inputs into bamCompare
    SAMPLE_DICT = {}
    for chip, inputs in zip(CHIPBAMS, INPUTBAMS):
        key = chip
        value = inputs
        SAMPLE_DICT[key] = value

else:
    E.warn("design.tsv is not located within the folder")
    INPUTBAMS = []
    CHIPBAMS = []


#########################################################################
# Connect to database
#########################################################################

def connect():
    '''
    Setup a connection to an sqlite database
    '''

    dbh = sqlite3.connect(PARAMS['database'])
    return dbh

###########################################################################
# start of pipelined tasks
# 1) Preprocessing Steps - Filter bam files & generate bam stats
###########################################################################


@transform("design.tsv", suffix(".tsv"), ".load")
def loadDesignTable(infile, outfile):
    ''' load design.tsv to database '''
    P.load(infile, outfile)


#####################################################
# makeTagDirectory Inputs
#####################################################

@active_if(PARAMS['homer'])
@follows(mkdir("homer"))
@follows(mkdir("homer/Tag.dir"))
@follows(loadDesignTable)
@transform(INPUTBAMS, regex("(.*).bam"),
           r"homer/Tag.dir/\1/\1.txt")
def makeTagDirectoryInput(infile, outfile):
    '''
    This will create a tag file for each bam file
    for a CHIP-seq experiment
    '''

    bamstrip = infile.strip(".bam")
    samfile = bamstrip + ".sam"

    statement = '''
                   samtools view %(infile)s > homer/Tag.dir/%(samfile)s;
                   cd homer/Tag.dir/ ;
                   makeTagDirectory %(bamstrip)s
                   %(samfile)s
                   -genome %(homer_maketagdir_genome)s -checkGC
                   &> %(bamstrip)s.makeTagInput.log;
                   touch %(bamstrip)s/%(bamstrip)s.txt'''

    P.run()


#####################################################
# makeTagDirectory ChIPs
#####################################################


@active_if(PARAMS['homer'])
@follows(mkdir("homer"))
@follows(mkdir("homer/Tag.dir"))
@follows(makeTagDirectoryInput)
@transform(CHIPBAMS, regex("(.*).bam"),
           r"homer/Tag.dir/\1/\1.txt")
def makeTagDirectoryChips(infile, outfile):
    '''
    This will create a tag file for each bam file
    for a CHIP-seq experiment
    '''

    bamstrip = infile.strip(".bam")
    samfile = bamstrip + ".sam"

    statement = '''
                   samtools view %(infile)s > homer/Tag.dir/%(samfile)s;
                   cd homer/Tag.dir/;
                   makeTagDirectory %(bamstrip)s
                   %(samfile)s
                   -genome %(homer_maketagdir_genome)s -checkGC
                   &> %(bamstrip)s.makeTagChip.log;
                   touch %(bamstrip)s/%(bamstrip)s.txt'''

    P.run()


@active_if(PARAMS['homer'])
@transform((makeTagDirectoryChips),
           regex("homer/Tag.dir/(.*)/(.*).txt"),
           r"homer/Tag.dir/\1/regions.txt")
def findPeaks(infile, outfile):

    '''
    This function will find peaks in your samples.

    Arguments
    ---------
    infiles : string
         this is a list of tag directories
    directory: string
         This is the directory where the tag file will be placed
    '''

    directory = infile.strip(".txt")
    _, directory, _ = directory.split("/")
    bamfile = directory + ".bam"

    df_slice = df[df['bamReads'] == bamfile]
    input_bam = df_slice['bamControl'].values[0]
    input_bam = input_bam.strip(".bam")

    statement = '''cd homer/Tag.dir/;
                   findPeaks %(directory)s -style %(homer_findpeaks_style)s -o %(homer_findpeaks_output)s
                   %(homer_findpeaks_options)s -i %(input_bam)s &> %(directory)s.findpeaks.log'''
    P.run()


@active_if(PARAMS['homer'])
@transform(findPeaks,
           regex("homer/Tag.dir/(.*)/regions.txt"),
           r"homer/Tag.dir/\1/\1.bed")
def bedConversion(infile, outfile):

    '''
    The peaks identified will be converted to a bed file for further downstream
    processing outside of this pipeline.
    '''

    statement = '''pos2bed.pl %(homer_bed_options)s %(infile)s > %(outfile)s'''

    P.run()


@active_if(PARAMS['homer'])
@transform(findPeaks,
           regex("homer/Tag.dir/(.*)/regions.txt"),
           r"homer/Tag.dir/\1/annotate.txt")
def annotatePeaks(infile, outfile):

    '''
    The peaks identified in your tag directory will be annotated according
    to the specified genome.
    '''

    statement = '''annotatePeaks.pl %(infile)s %(homer_annotatepeaks_genome)s &> Annotate.log > %(outfile)s'''

    P.run()


@active_if(PARAMS['homer'])
@transform(findPeaks,
           regex("homer/tag.dir/(.*)/regions.txt"),
           r"homer/motif.dir/\1/motifs.txt")
def findMotifs(infile, outfile):

    '''
    This will find known motifs enrched in yopur peaks lists using the
    Jaspar database.
    '''

    _, directory, _ = infile.split("/")

    statement = '''findMotifsGenome.pl %(infile)s %(homer_motif_genome)s homer/motif.dir/%(directory)s -size %(homer_motif_size)i
                   &> Motif.log'''

    P.run()


@active_if(PARAMS['homer'])
@active_if(PARAMS['homer_diffannotat_raw'])
@follows(mkdir("homer/raw_annotate.dir"))
@merge(makeTagDirectoryChips, "homer/raw_annotate.dir/countTable.peaks.txt")
def annotatePeaksRaw(infiles, outfile):

    '''
    This function will annotate peaks according to the genome that is specified in the
    pipeline.ini section. It will take an unprocessed Tag directory as an input.
    '''

    directories = []

    for infile in infiles:
        directory = infile.split("/")[1]
        directories.append("homer/Tag.dir/" + directory + "/")

    directories = " ".join(directories)

    statement = '''annotatePeaks.pl %(homer_annotate_raw_region)s %(homer_annotate_raw_genome)s
                   -d %(directories)s > homer/raw_annotate.dir/countTable.peaks.txt'''

    P.run()


@active_if(PARAMS['homer'])
@active_if(PARAMS['homer_diff_expr'])
@follows(mkdir("homer/diffExprs.dir"))
@transform(annotatePeaksRaw,
           suffix(".peaks.txt"),
           r"homer/raw_annotate.dir/\1.diffexprs.txt")
def getDiffExprs(infile, outfile):

    '''
    Once the peaks have been annotated and the reads counted, differential
    expression is then performed.
    '''
    # in the future this should be read from the design file AC 11/12/2017

    statement = '''getDiffExpression.pl %(infile)s
                  %(homer_diff_expr_options)s %(homer_diff_expr_group)s
                  > homer/diffExprs.dir/diffOutput.txt'''

    P.run()


@active_if(PARAMS['homer'])
@active_if(PARAMS['homer_diff_repeats'])
@follows(mkdir("homer/Replicates.dir"))
@follows(makeTagDirectoryChips)
@originate("homer/Replicates.dir/outputPeaks.txt")
def getDiffPeaksReplicates(outfile):

    '''
    The function will determine the statistically enriched peaks accross
    replicates.

    The output of the function is a homer peak file that contains several
    columns of annotation, normalised read counts and differential enriched statistics
    from DESEq2.
    '''
    replicates = set(df["Replicate"])

    for x in replicates:
        subdf = df[df["Replicate"] == x]

        bams = subdf["bamReads"].values

        bam_strip = []
        for bam in bams:
            bam = bam.strip(".bam") + "/"
            bam_strip.append(bam)

    bam_strip = " ".join(bam_strip)

    inputs = subdf["bamControl"].values

    input_strip = []
    for inp in inputs:
        inp = inp.strip(".bam") + "/"
        input_strip.append(inp)

    input_strip = " ".join(input_strip)

    statement = '''getDifferentialPeaksReplicates.pl -t %(bam_strip)s
                       -i %(input_strip)s -genome %(homer_diff_repeats_genome)s %(homer_diff_repeats_options)s>
                       homer/Replicates.dir/Repeat-%(x)s.outputPeaks.txt'''

    P.run()


##################################################################################################
# This is the section where the deeptool (http://deeptools.readthedocs.io/en/latest/index.html#)
# deepTools is a suite of python tools particularly developed for the efficient analysis
# of high-throughput sequencing data, such as ChIP-seq, RNA-seq or MNase-seq
# Functions are specified
##################################################################################################


@active_if(PARAMS['deeptools'])
@follows(mkdir("deepTools/Plot.dir/Coverage.dir"))
@follows(loadDesignTable)
@merge([CHIPBAMS, INPUTBAMS], "deepTools/Plot.dir/Coverage.dir/coverage_plot.eps")
def coverage_plot(infiles, outfile):

    '''
    This tool is useful to assess the sequencing depth of a given sample.
    It samples 1 million bp, counts the number of overlapping reads and
    can report a histogram that tells you how many bases are covered how
    many times. Multiple BAM files are accepted, but they all should
    correspond to the same genome assembly.
    '''

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_ignore_dups']:
        duplicates = "--ignoreDuplicates"
    elif not PARAMS['deep_ignore_dups']:
        duplicates = ""
    else:
        raise ValueError('''Please set a ignore_dups value in the
                   pipeline.ini''')

    statement = '''plotCoverage -b %(infile)s
                   --plotFile %(outfile)s
                   --plotTitle "coverage_plot"
                   --outRawCounts deepTools/Plot.dir/Coverage.dir/coverage_plot.tab
                   %(duplicates)s
                   --minMappingQuality %(deep_mapping_qual)s'''

    P.run()


@follows(coverage_plot)
@active_if(PARAMS['deeptools'])
@follows(mkdir("deepTools/Plot.dir/Fingerprint.dir"))
@follows(loadDesignTable)
@merge([CHIPBAMS, INPUTBAMS],
       "deepTools/Plot.dir/Fingerprint.dir/fingerprints.eps")
def fingerprint_plot(infiles, outfile):

    '''
    This quality control will most likely be of interest for you
    if you are dealing with ChIP-seq samples as a pressing question
    in ChIP-seq experiments is Did my ChIP work?, i.e. did the
    antibody-treatment enrich sufficiently so that the ChIP signal
    can be separated from the background signal? (After all, around
    90% of all DNA fragments in a ChIP experiment will represent
    the genomic background).

    This tool samples indexed BAM files and plots a profile of
    cumulative read coverages for each. All reads overlapping a
    window (bin) of the specified length are counted; these counts
    are sorted and the cumulative sum is finally plotted.
    '''

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_ignore_dups']:
        duplicates = "--ignoreDuplicates"
    elif not PARAMS['deep_ignore_dups']:
        duplicates = ""
    else:
        raise ValueError('''Please set a ignore_dups value in the
                   pipeline.ini''')

    statement = '''plotFingerprint -b %(infile)s
                   --plotFile %(outfile)s
                   --plotTitle "Fingerprints of samples"
                   --outRawCounts deepTools/Plot.dir/Fingerprint.dir/fingerprints_plot.tab
                   %(duplicates)s
                   --minMappingQuality %(deep_mapping_qual)s'''

    P.run()


@follows(fingerprint_plot)
@active_if(PARAMS['deeptools'])
@active_if(PARAMS['deep_paired_end'])
@follows(mkdir("deepTools/Plot.dir/FragmentSize.dir"))
@follows(loadDesignTable)
@merge([CHIPBAMS, INPUTBAMS],
       "deepTools/Plot.dir/FragmentSize.dir/FragmentSize.png")
def fragment_size(infiles, outfile):

    '''
    This tool calculates the fragment sizes for read pairs
    given a BAM file from paired-end sequencing.Several regions
    are sampled depending on the size of the genome and number
    of processors to estimate thesummary statistics on the fragment
    lengths. Properly paired reads are preferred for computation,
    i.e., it will only use discordant pairs if no concordant
    alignments overlap with a given region. The default setting
    simply prints the summary statistics to the screen.
    '''

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_logscale']:
        logscale = ("--logScale %s") % (PARAMS['deep_logscale'])
    else:
        logscale = ""

    statement = '''bamPEFragmentSize -b %(infile)s
                   --histogram %(outfile)s
                   %(logscale)s'''

    P.run()


@follows(fragment_size)
@active_if(PARAMS['deeptools'])
@active_if(PARAMS['deep_bam_coverage'])
@follows(mkdir("deepTools/Bwfiles.dir/bamCoverage.dir"))
@transform(TOTALBAMS, regex("(.*).bam"),
           r"deepTools/Bwfiles.dir/bamCoverage.dir/\1.bw")
def bamCoverage(infiles, outfile):

    '''
    This tool takes an alignment of reads or fragments as
    input (BAM file) and generates a coverage track (bigWig
    or bedGraph) as output. The coverage is calculated as
    the number of reads per bin, where bins are short
    consecutive counting windows of a defined size. It is
    possible to extended the length of the reads to better
    reflect the actual fragment length. bamCoverage offers
    normalization by scaling factor, Reads Per Kilobase per
    Million mapped reads (RPKM), and 1x depth (reads per
    genome coverage, RPGC).
    '''

    if PARAMS['deep_ignore_norm'] is not "":
        normalise = '--ignoreForNormalization '
        norm_value = PARAMS['deep_ignore_norm']

    else:
        normalise = ''
        norm_value = ''

    if PARAMS['deep_extendreads']:
        extend = '--extendReads'
    elif not PARAMS['deep_extendreads']:
        extend = ''
    else:
        raise ValueError('''Please set the extendreads to a value 0 or 1''')

    statement = '''bamCoverage --bam %(infiles)s
                   -o %(outfile)s
                   -of bigwig
                   --binSize %(deep_binsize)s
                   %(normalise)s %(norm_value)s
                   %(extend)s
                   %(deep_bamcoverage_options)s'''

    P.run()


@follows(fragment_size)
@active_if(PARAMS['deeptools'])
@active_if(PARAMS['deep_bam_compare'])
@follows(loadDesignTable)
@follows(mkdir("deepTools/Bwfiles.dir/bamCompare.dir"))
@transform(CHIPBAMS,
           suffix('.bam'),
           add_inputs(SAMPLE_DICT),
           r"deepTools/Bwfiles.dir/bamCompare.dir/\1.bw")
def bamCompare(infiles, outfile):

    '''
    This tool compares two BAM files based on the number of
    mapped reads. To compare the BAM files, the genome is
    partitioned into bins of equal size, then the number of reads
    found in each bin is counted per file, and finally a summary
    value is reported. This value can be the ratio of the number
    of reads per bin, the log2 of the ratio, or the difference.
    This tool can normalize the number of reads in each BAM file
    using the SES method proposed by Diaz et al. (2012) Normalization,
    bias correction, and peak calling for ChIP-seq. Statistical
    Applications in Genetics and Molecular Biology, 11(3).
    Normalization based on read counts is also available. The
    output is either a bedgraph or bigWig file containing the
    bin location and the resulting comparison value. By default,
    if reads are paired, the fragment length reported in the
    BAM file is used. Each mate, however, is treated independently
    to avoid a bias when a mixture of concordant and discordant
    pairs is present. This means that each end will be extended
    to match the fragment length.
    '''

    chipbam = infiles[0]
    inputbams = infiles[1]
    inputbam = inputbams[chipbam]

    statement = '''
                   bamCompare -b1 %(chipbam)s
                       -b2 %(inputbam)s
                       -o %(outfile)s
                       -of bigwig
                       %(deep_bamcompare_options)s'''

    P.run()


@follows(bamCompare)
@active_if(PARAMS['deeptools'])
@follows(loadDesignTable)
@follows(mkdir("deepTools/Summary.dir"))
@merge([CHIPBAMS, INPUTBAMS], "deepTools/Summary.dir/Bam_Summary.npz")
def multiBamSummary(infiles, outfile):

    '''
    multiBamSummary computes the read coverages for genomic regions
    for typically two or more BAM files. The analysis can be
    performed for the entire genome by running the program in
    bins mode. If you want to count the read coverage for
    specific regions only, use the BED-file mode instead.
    The standard output of multiBamSummary is a compressed
    numpy array (.npz). It can be directly used to calculate
    and visualize pairwise correlation values between the read
    coverages using the tool plotCorrelation. Similarly,
    plotPCA can be used for principal component analysis of
    the read coverages using the .npz file. Note that using
    a single bigWig file is only recommended if you want to
    produce a bedGraph file (i.e., with the --outRawCounts
    option; the default output file cannot be used by ANY
    deepTools program if only a single file was supplied!).
    '''

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_mode_setting'] == 'None':
        mode_set = 'bins'
        mode_region = ''
    else:
        mode_set = 'BED-file --BED '
        mode_region = PARAMS['deep_mode_setting']

    if PARAMS['deep_ignore_dups']:
        duplicates = "--ignoreDuplicates"
    elif not PARAMS['deep_ignore_dups']:
        duplicates = ""
    else:
        raise ValueError('''Please set a ignore_dups value in the
                   pipeline.ini''')

    statement = '''
                   multiBamSummary %(mode_set)s %(mode_region)s
                   -b %(infile)s
                   -o %(outfile)s
                   --outRawCounts deepTools/Summary.dir/Bam_Summary.tab
                   --minMappingQuality %(deep_mapping_qual)s
                   %(deep_summary_options)s'''

    P.run()


@active_if(PARAMS['deeptools'])
@merge([bamCoverage, bamCompare], "deepTools/Summary.dir/bw_summary.npz")
def multiBwSummary(infiles, outfile):

    '''
    This performs and summary accross all of the big wig files
    and is similar to that impliemnted for the multi bam Summary.
    '''

    infiles = " ".join(infiles)

    if PARAMS['deep_mode_setting'] == 'None':
        mode_set = 'bins'
        mode_region = ''
    else:
        mode_set = 'BED-file --BED '
        mode_region = PARAMS['deep_mode_setting']

    statement = '''multiBigwigSummary %(mode_set)s %(mode_region)s
                   -b %(infiles)s
                   -out %(outfile)s
                   --outRawCounts deepTools/Summary.dir/Bw_Summary.tab
                   %(deep_summary_options)s'''

    P.run()


@active_if(PARAMS['deeptools'])
@follows(mkdir("deepTools/Plot.dir/Summary.dir/"))
@transform((multiBamSummary, multiBwSummary),
           regex("\S+/(\S+).npz"),
           r"deepTools/Plot.dir/Summary.dir/\1corr")
def plotCorrelation(infiles, outfile):

    '''
    Tool for the analysis and visualization of sample
    correlations based on the output of multiBamSummary
    or multiBigwigSummary. Pearson or Spearman methods
    are available to compute correlation coefficients.
    Results can be saved as multiple scatter plots depicting
    the pairwise correlations or as a clustered heatmap,
    where the colors represent the correlation coefficients
    and the clusters are joined using the Nearest Point
    Algorithm (also known as single). Optionally,
    the values can be saved as tables, too.
    '''

    if PARAMS['deep_plot'] == 'heatmap':
        colormap = ("--colorMap %s") % (PARAMS['deep_colormap'])
    else:
        colormap = ""

    statement = '''plotCorrelation -in %(infiles)s -o %(outfile)s
                   --corMethod %(deep_cormethod)s -p %(deep_plot)s
                   %(colormap)s
                   --plotFileFormat %(deep_filetype)s
                   --skipZeros %(deep_plot_options)s'''
    P.run()


@active_if(PARAMS['deeptools'])
@transform((multiBamSummary, multiBwSummary),
           regex("\S+/(\S+).npz"),
           r"deepTools/Plot.dir/Summary.dir/\1PCA")
def plotPCA(infiles, outfile):

    '''
    This will plot a PCA of the samples.
    '''
    statement = '''plotPCA -in %(infiles)s -o %(outfile)s
                   --plotFileFormat %(deep_filetype)s
                   %(deep_plot_options)s'''
    P.run()


@active_if(PARAMS['deeptools'])
@follows(mkdir("deepTools/Plot.dir/matrix.dir/"))
@merge([bamCoverage, bamCompare],
       "deepTools/Plot.dir/matrix.dir/matrix.gz")
def computeMatrix(infile, outfile):

    '''
    This computes a count matrix for downstream processing of the data.
    '''
    infile = " ".join(infile)

    if 'reference-point' in PARAMS['deep_startfactor']:
        reference_point = '--referencePoint'
        regions = PARAMS['deep_regions']
        region_length = " "
    elif "scale-regions" in PARAMS['deep_startfactor']:
        reference_point == '--regionBodyLength'
        regions = " "
        region_length = PARAMS['deep_region_length']
    else:
        raise(ValueError("Please supply a valid startfactor"))

    if ".gz" in PARAMS['deep_bedfile']:
        infile = PARAMS['deep_bedfile']
        bedfile = IOTools.openFile(infile, "r")
    else:
        bedfile = PARAMS['deep_bedfile']

    if PARAMS['deep_brslength'] is not "":
        upstream = ("--upstream %s") % (PARAMS['deep_brslength'])

    if PARAMS['deep_arslength'] is not "":
        downstream = ("--downstream %s") % (PARAMS['deep_arslength'])

    if PARAMS['deep_matrix_bin_size'] is not "":
        binsize = ("--binSize %s") % (PARAMS['deep_matrix_bin_size'])
    else:
        binsize = ""

    if PARAMS['deep_out_namematrix'] is not "":
        outmatrix = ("--outFileNameMatrix %s") % (PARAMS['deep_out_namematrix'])
    else:
        outmatrix = ""

    if PARAMS['deep_out_sorted'] is not "":
        sortedfile = ("--outFileSortedRegions %s") % (PARAMS['deep_out_sorted'])
    else:
        sortedfile = ""

    statement = '''computeMatrix %(deep_startfactor)s -S %(infile)s
                   -R %(bedfile)s
                   %(reference_point)s %(regions)s %(region_length)s
                   %(upstream)s
                   %(downstream)s
                   %(binsize)s
                   --skipZeros
                   -o %(outfile)s
                   %(outmatrix)s
                   %(sortedfile)s'''
    P.run()


@active_if(PARAMS['deeptools'])
@transform(computeMatrix,
           regex("\S+/\S+/\S+/(\S+).gz"),
           r"deepTools/Plot.dir/matrix.dir/\1_heatmap.eps")
def plotHeatmap(infile, outfile):

    '''
    This tool creates a heatmap for scores associated with genomic regions.
    The program requires a matrix file generated by the tool computeMatrix.
    '''

    infile = "".join(infile)

    statement = '''plotHeatmap -m %(infile)s
                   -o %(outfile)s
                   --outFileNameMatrix %(deep_out_namematrix)s
                   --outFileSortedRegions %(deep_out_sorted)s
                   --dpi %(deep_dpi)s
                   --colorMap %(deep_colormap)s
                   --kmeans %(deep_kmeans)s
                   --legendLocation %(deep_legendlocation)s
                   --refPointLabel %(deep_refpointlabel)s'''

    P.run()


@active_if(PARAMS['deeptools'])
@transform(computeMatrix,
           regex("\S+/\S+/\S+/(\S+).gz"),
           r"deepTools/Plot.dir/matrix.dir/\1_profile.eps")
def plotProfile(infile, outfile):

    '''
    This tool creates a profile plot for scores over sets of genomic
    regions. Typically, these regions are genes, but any other regions
    defined in BED will work. A matrix generated by computeMatrix
    is required.
    '''

    infile = "".join(infile)

    if PARAMS['deep_pergroup'] is not "":
        pergroup = ("--perGroup %s") % (PARAMS['deep_pergroup'])
    else:
        pergroup = ""

    statement = '''plotProfile -m %(infile)s
                   -o %(outfile)s
                   --kmeans %(deep_kmeans)s
                   --plotType %(deep_plottype)s
                   --dpi %(deep_dpi)s
                   %(pergroup)s
                   --legendLocation %(deep_legendlocation)s
                   --refPointLabel %(deep_refpointlabel)s'''

    P.run()


# ---------------------------------------------------
# Generic pipeline tasks


@follows(loadDesignTable,
         bedConversion,
         annotatePeaks,
         annotatePeaksRaw,
         getDiffExprs,
         getDiffPeaksReplicates,
         coverage_plot,
         fingerprint_plot,
         bamCompare,
         bamCoverage,
         multiBamSummary,
         multiBwSummary,
         plotCorrelation,
         plotPCA,
         computeMatrix,
         plotHeatmap,
         plotProfile)
def standard():
    '''a target without the motif generation'''
    pass


@follows(makeTagDirectoryInput,
         loadDesignTable,
         bedConversion,
         annotatePeaks,
         annotatePeaksRaw,
         getDiffExprs,
         getDiffPeaksReplicates,
         findMotifs,
         coverage_plot,
         fingerprint_plot,
         bamCompare,
         bamCoverage,
         multiBamSummary,
         multiBwSummary,
         plotCorrelation,
         plotPCA,
         computeMatrix,
         plotHeatmap,
         plotProfile)
def full():
    pass


@follows(mkdir("Jupyter_report.dir"))
def renderJupyterReport():
    '''build Jupyter notebook report'''

    report_path = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                               'pipeline_docs',
                                               'pipeline_homer',
                                               'Jupyter_report'))

    statement = ''' cp %(report_path)s/* Jupyter_report.dir/ ; cd Jupyter_report.dir/;
                    jupyter nbconvert --ExecutePreprocessor.timeout=None
                    --to html --execute *.ipynb;
                 '''

    P.run()


# We will implement this when the new version of multiqc is available
@follows(mkdir("MultiQC_report.dir"))
@originate("MultiQC_report.dir/multiqc_report.html")
def renderMultiqc(infile):
    '''build mulitqc report'''

    statement = '''LANG=en_GB.UTF-8 multiqc . -f;
                   mv multiqc_report.html MultiQC_report.dir/'''

    P.run()


@follows(renderJupyterReport)
def build_report():
    pass


def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)


if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
