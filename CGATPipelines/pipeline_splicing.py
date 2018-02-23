"""===========================
Pipeline Splicing
===========================


Overview
========

This pipeline enables differential exon usage testing through the
implementation of
* rMATS-turbo
* DEXSeq

rMATS is a computational tool to detect differential alternative splicing
events from RNA-Seq data. The statistical model of MATS calculates the P-value
and false discovery rate that the difference in the isoform ratio of a gene
between two conditions exceeds a given user-defined threshold. From the
RNA-Seq data, MATS can automatically detect and analyze alternative splicing
events corresponding to all major types of alternative splicing patterns.
MATS handles replicate RNA-Seq data from both paired and unpaired study design.

DEXSeq is a bioconductor R package to detect differential exon usage between
conditions in RNA-Seq experiments. Relative exon usage is defined as
(number of reads from exon)/(number of reads from its gene). It uses a similar
model to DESeq to estimate dispersion parameters prior to differential
testing.

Principal targets
-----------------

full
    compute all functions

Optional targets
----------------

permute
    repeat rMATS after permuting sample group labels


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

   python <srcdir>/pipeline_splicing.py config

Input files
-----------

".bam" files generated using STAR or Tophat2. Other mappers
may also work. Bam indexation is not required.

Design_files ("*.design.tsv") are used to specify sample variates. The
minimal design file is shown below, where include specifies if the
sample should be included in the analysis, group specifies the sample
group and pair specifies whether the sample is paired. Note, multiple
design files may be included, for example so that multiple models can
be fitted to different subsets of the data

(tab-seperated values)

sample    include    group    pair
WT-1-1    1    WT    0
WT-1-2    1    WT    0
Mutant-1-1    1    Mutant    0
Mutant-1-2    1    Mutant    0

The pipeline can only handle comparisons between two conditions with
replicates. If further comparisons are needed, further design files
should be used.

Requirements
------------

The pipeline requires the results from
:doc:`pipeline_annotations`. Set the configuration variable
:py:data:`annotations_database` and :py:data:`annotations_dir`.

Requirements:

* samtools
* DEXSeq
* rMATS-turbo
* pysam
* HTSeqCounts

Pipeline output
===============

For each experiment, the output from rMATS is placed in the results.dir
folder. Each experiment is found in a subdirectory named designfilename.dir

rMATS output is further described here:
http://rnaseq-mats.sourceforge.net/user_guide.htm



Glossary
========

.. glossary::


Code
====

"""
from ruffus import *
import sys
import os
import glob
import sqlite3
import pandas as pd
from rpy2.robjects import r as R
import CGAT.BamTools as BamTools
import CGATCore.Experiment as E
from CGATCore import Pipeline as P
import CGATPipelines.PipelineTracks as PipelineTracks
import CGATPipelines.PipelineSplicing as PipelineSplicing


###################################################################
###################################################################
###################################################################
# Load options and annotations
###################################################################

# load options from the config file
PARAMS = P.get_parameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"])

# add configuration values from associated pipelines
PARAMS = P.PARAMS
PARAMS.update(P.peek_parameters(
    PARAMS["annotations_dir"],
    "genesets",
    prefix="annotations_",
    update_interface=True,
    restrict_interface=True))  # add config values from associated pipelines

# The DEXSeq R directory contains important python helper functions
PYTHONSCRIPTSDIR = R('''
    f = function(){
    pythonScriptsDir = system.file("python_scripts", package="DEXSeq")
    }
    f()''').tostring()


###################################################################
###################################################################
###################################################################
# Utility functions
###################################################################

def connect():
    '''Connect to database (sqlite by default)

    This method also attaches to helper databases.
    '''

    dbh = sqlite3.connect(PARAMS["database_name"])
    statement = '''ATTACH DATABASE '%s' as annotations''' % (
        PARAMS["annotations_database"])
    cc = dbh.cursor()
    cc.execute(statement)
    cc.close()

    return dbh


class MySample(PipelineTracks.Sample):
    attributes = tuple(PARAMS["attributes"].split(","))


TRACKS = PipelineTracks.Tracks(MySample).loadFromDirectory(
    glob.glob("*.bam"), "(\S+).bam")

Sample = PipelineTracks.AutoSample
DESIGNS = PipelineTracks.Tracks(Sample).loadFromDirectory(
    glob.glob("*.design.tsv"), "(\S+).design.tsv")


###################################################################
###################################################################
###################################################################
# DEXSeq workflow
###################################################################

@mkdir("results.dir")
@files(PARAMS["annotations_interface_geneset_all_gtf"],
       "geneset_flat.gff")
def buildGff(infile, outfile):
    '''Creates a gff for DEXSeq

    This takes the gtf and flattens it to an exon based input
    required by DEXSeq. The required python script is provided by DEXSeq
    and uses HTSeqCounts.

    Parameters
    ----------

    infile : string
       Input filename in :term:`gtf` format

    outfile : string
        A :term:`gff` file for use in DEXSeq

    annotations_interface_geneset_all_gtf : string
       :term:`PARAMS`. Filename of :term:`gtf` file containing
       all ensembl annotations
    '''

    tmpgff = P.getTempFilename(".")
    statement = "gunzip -c %(infile)s > %(tmpgff)s"
    P.run(statement)

    ps = PYTHONSCRIPTSDIR
    statement = '''python %(ps)s/dexseq_prepare_annotation.py
                %(tmpgff)s %(outfile)s'''
    P.run(statement)

    os.unlink(tmpgff)


@mkdir("counts.dir")
@transform(glob.glob("*.bam"),
           regex("(\S+).bam"),
           add_inputs(buildGff),
           r"counts.dir/\1.txt")
def countDEXSeq(infiles, outfile):
    '''create counts for DEXSeq

    Counts bam reads agains exon features in flattened gtf.
    The required python script is provided by DEXSeq
    and uses HTSeqCounts.

    Parameters
    ----------

    infile[0]: string
        :term:`bam` file input

    infile[1]: string
        :term:`gff` output from buildGff function

    outfile : string
        A :term:`txt` file containing results

    DEXSeq_strandedness : string
       :term:`PARAMS`. Specifies strandedness, options
       are 'yes', 'no' and 'reverse'

    '''

    infile, gfffile = infiles
    ps = PYTHONSCRIPTSDIR
    if BamTools.isPaired(infile):
        paired = "yes"
    else:
        paired = "no"
    strandedness = PARAMS["DEXSeq_strandedness"]

    statement = '''python %(ps)s/dexseq_count.py
    -p %(paired)s
    -s %(strandedness)s
    -r pos
    -f bam  %(gfffile)s %(infile)s %(outfile)s'''
    P.run(statement)


@collate(countDEXSeq,
         regex("counts.dir/([^.]+)\.txt"),
         r"summarycounts.tsv")
def aggregateExonCounts(infiles, outfile):
    ''' Build a matrix of counts with exons and tracks dimensions.

    Uses `combine_tables.py` to combine all the `txt` files output from
    countDEXSeq into a single :term:`tsv` file named
    "summarycounts.tsv". A `.log` file is also produced.

    Parameters
    ---------
    infiles : list
        a list of `tsv.gz` files from the counts.dir that were the
        output from dexseq_count.py

    outfile : string
        a filename denoting the file containing a matrix of counts with genes
        as rows and tracks as the columns - this is a `tsv.gz` file      '''

    infiles = " ".join(infiles)
    statement = '''python %(scriptsdir)s/combine_tables.py
    --columns=1
    --take=2
    --use-file-prefix
    --regex-filename='([^.]+)\.txt'
    --no-titles
    --log=%(outfile)s.log
    %(infiles)s
    > %(outfile)s '''

    P.run(statement)


@follows(aggregateExonCounts)
@mkdir("results.dir/DEXSeq")
@subdivide(["%s.design.tsv" % x.asFile().lower() for x in DESIGNS],
           regex("(\S+).design.tsv"),
           r"results.dir/DEXSeq/\1_results.tsv")
def runDEXSeq(infile, outfile):
    ''' run DEXSeq command

    DEXSeq is run using the counts2table from the
    CGAT code collection. Output is standardised to
    correspond to differential gene expression output
    from DESeq2 or Sleuth.

    Will currently only test 2 groups.

    Parameters
    ---------
    infiles : string
        filename and path of design file

    outfile : string
        a filename denoting the file containing a standard results
        output with full results of all tested exons

    DEXSeq_model_% : string
    DEXSeq_contrast_% : string
    DEXSeq_refgroup_% : string
       :term:`PARAMS`. Specifies model, contrast and reference
       group for DEXSeq analysis
    '''

    outdir = os.path.dirname(outfile)
    countsdir = "counts.dir/"
    gfffile = os.path.abspath("geneset_flat.gff")
    dexseq_fdr = 0.05
    design = infile.split('.')[0]
    model = PARAMS["DEXSeq_model_%s" % design]
    contrast = PARAMS["DEXSeq_contrast_%s" % design]
    refgroup = PARAMS["DEXSeq_refgroup_%s" % design]

    statement = '''
    python %%(scriptsdir)s/counts2table.py
    --design-tsv-file=%(infile)s
    --output-filename-pattern=%(outdir)s/%(design)s
    --log=%(outdir)s/%(design)s_DEXSeq.log
    --method=dexseq
    --fdr=%(dexseq_fdr)s
    --model=%(model)s
    --dexseq-counts-dir=%(countsdir)s
    --contrast=%(contrast)s
    -r %(refgroup)s
    --dexseq-flattened-file=%(gfffile)s
    > %(outfile)s;
    ''' % locals()

    P.run(statement)


###################################################################
###################################################################
###################################################################
# rMATS workflow
###################################################################

@mkdir("results.dir/rMATS")
@subdivide(["%s.design.tsv" % x.asFile().lower() for x in DESIGNS],
           regex("(\S+).design.tsv"),
           add_inputs(PARAMS["annotations_interface_geneset_all_gtf"]),
           [r"results.dir/rMATS/\1.dir/%s.MATS.JC.txt" % x for x in ["SE", "A5SS", "A3SS", "MXE", "RI"]])
def runMATS(infile, outfiles):
    '''run rMATS-turbo

    Runs rMATS command.

    Parameters
    ---------
    infiles[0] : string
        filename and path of design file

    infiles[1] : string
        filename and path of :term:`gtf` file

    outfile : list
        a list of filenames denoting the file containing a standard results
        output with full results for all five tested differential exon
        usage conditions.

    MATS_libtype : string
       :term:`PARAMS`. Specifies library type. Can be "fr-firstrand",
       "fr-secondstrand" or "fr-unstranded"
    '''

    design, gtffile = infile
    strand = PARAMS["MATS_libtype"]
    outdir = os.path.dirname(outfiles[0])
    if not os.path.exists(outdir):
        os.makedirs(outdir)

    PipelineSplicing.runRMATS(gtffile=gtffile, designfile=design,
                              pvalue=PARAMS["MATS_cutoff"],
                              strand=strand, outdir=outdir)


@follows(runMATS)
@transform(runMATS,
           regex("results.dir/rMATS/(\S+).dir/(\S+).MATS.JC.txt"),
           r"results.dir/rMATS/rMATS_\1_\2_JC.load")
def loadMATS(infile, outfile):
    '''load RMATS results into relational database

    Loads rMATS results into relational database.
    Continues if table empty.

    Parameters
    ----------
    infile: term:`tsv` file containing one type of rMATS results.
    outfile: .load file
    '''
    try:
        P.load(infile, outfile)
    except:
        P.touch(outfile)


@collate(runMATS,
         regex("results.dir/rMATS/(\S+).dir/\S+.MATS.JC.txt"),
         r"results.dir/rMATS/rMATS_\1_results.summary")
def collateMATS(infiles, outfile):
    '''collates summary from all events

    Collates number of events below FDR threshold from all
    five events into simple table

    Parameters
    ----------
    infiles: list
        list of results files from rMATS

    MATS_fdr : string
       :term:`PARAMS`. User specified threshold for result counting

    outfile: string
        summary file containing number of results below FDR threshold
    '''

    indir = os.path.dirname(infiles[1])
    collate = []
    with open(indir + "/b1.txt", "r") as f:
        collate.append(f.readline())
    with open(indir + "/b2.txt", "r") as f:
        collate.append(f.readline())
    for event in ["SE", "A5SS", "A3SS", "MXE", "RI"]:
        temp = pd.read_csv("%s/%s.MATS.JC.txt" %
                           (indir, event), sep='\t')
        collate.append(str(len(temp[temp['FDR'] < PARAMS['MATS_fdr']])))
    with open(outfile, "w") as f:
        f.write("Group1\tGroup2\tSE\tA5SS\tA3SS\tMXE\tRI\n")
        f.write('\t'.join(collate))


@transform(collateMATS,
           suffix(".summary"),
           ".load")
def loadCollateMATS(infile, outfile):
    '''load rMATS summary into relational database

    Loads rMATS summary results into relational database.

    Parameters
    ----------
    infile: file containing summary table of rMATS results
    outfile: .load file
    '''

    P.load(infile, outfile)


@active_if(PARAMS["permute"] == 1)
@subdivide(["%s.design.tsv" % x.asFile().lower() for x in DESIGNS],
           regex("(\S+).design.tsv"),
           r"results.dir/rMATS/\1.dir/permutations/run*.dir/init",
           r"results.dir/rMATS/\1.dir/permutations")
def permuteMATS(infile, outfiles, outdir):
    '''creates directories for permutation testing

    Creates directories for permutation testing and leaves dummy
    init file in directory (for timestamping)
    Only becomes active if :term:`PARAMS` permute is set to 1

    Parameters
    ----------
    infile: string
        name and path to design

    outfile: list
        list of unknown length, capturing all permutations
        retrospectively

    outdir: string
        directory to generate permutations in

    permutations : string
       :term:`PARAMS`. number of directories to be generated
    '''

    if not os.path.exists(outdir):
        os.makedirs(outdir)
    for i in range(0, PARAMS["permutations"]):
        if not os.path.exists("%s/run%i.dir" % (outdir, i)):
            os.makedirs("%s/run%i.dir" % (outdir, i))
        P.touch("%s/run%i.dir/init" % (outdir, i))


@transform(permuteMATS,
           regex("results.dir/rMATS/(\S+).dir/permutations/(\S+).dir/init"),
           add_inputs(PARAMS["annotations_interface_geneset_all_gtf"]),
           r"results.dir/rMATS/\1.dir/permutations/\2.dir/result.tsv",
           r"\1.design.tsv")
def runPermuteMATS(infiles, outfile, design):
    '''run rMATS-turbo permutation testing

    Runs rMATS command on permutations and then collates results into
    small summary table for each permutation

    Parameters
    ---------
    infiles[0] : string
        filename and path of design file

    infiles[1] : string
        filename and path of :term:`gtf` file

    outfile : :term:`tsv` file
        file containing summary results meeting the user-specified FDR
        threshold

    design : string
        name and path of design file

    MATS_libtype : string
       :term:`PARAMS`. Specifies library type. Can be "fr-firstrand",
       "fr-secondstrand" or "fr-unstranded"

    MATS_fdr : string
       :term:`PARAMS`. User specified threshold for result counting

    '''

    init, gtffile = infiles
    directory = os.path.dirname(init)
    strand = PARAMS["MATS_libtype"]
    outdir = os.path.dirname(outfile)
    if not os.path.exists(outdir):
        os.makedirs(outdir)

    PipelineSplicing.runRMATS(gtffile=gtffile, designfile=design,
                              pvalue=PARAMS["MATS_cutoff"],
                              strand=strand, outdir=directory, permute=1)

    collate = []
    with open(os.path.dirname(init) + "/b1.txt", "r") as f:
        collate.append(f.readline())
    with open(os.path.dirname(init) + "/b2.txt", "r") as f:
        collate.append(f.readline())
    for event in ["SE", "A5SS", "A3SS", "MXE", "RI"]:
        temp = pd.read_csv("%s/%s.MATS.JC.txt" %
                           (os.path.dirname(outfile), event), sep='\t')
        collate.append(str(len(temp[temp['FDR'] < PARAMS['MATS_fdr']])))
    with open(outfile, "w") as f:
        f.write("Group1\tGroup2\tSE\tA5SS\tA3SS\tMXE\tRI\n")
        f.write('\t'.join(collate))


@collate(runPermuteMATS,
         regex("results.dir/rMATS/(\S+).dir/permutations/\S+.dir/result.tsv"),
         r"results.dir/rMATS/rMATS_\1_permutations.summary")
def collatePermuteMATS(infiles, outfile):
    '''collates summary table of all permutations

    Collates number of events below FDR threshold from all
    permutation runs.

    Parameters
    ----------
    infiles: list
        list of rMATS result summaries from all permutation runs

    outfile: string
        summary file containing a table with all permutation run
        results
    '''

    collate = []
    for infile in infiles:
        collate.append(pd.read_csv(infile, sep='\t'))
    pd.concat(collate).to_csv(outfile, sep='\t', index=0)


@transform(collatePermuteMATS,
           suffix(".summary"),
           ".load")
def loadPermuteMATS(infile, outfile):
    '''load rMATS permutation results

    Loads rMATS permutation summary results into relational database.

    Parameters
    ----------
    infile: file containing summary table of rMATS permutation results
    outfile: .load file
    '''

    P.load(infile, outfile)


@mkdir("results.dir/sashimi")
@transform(runMATS,
           regex("results.dir/rMATS/(\S+).dir/(\S+).MATS.JC.txt"),
           add_inputs(r"\1.design.tsv"),
           r"results.dir/sashimi/\1.dir/\2")
def runSashimi(infiles, outfile):
    '''draws sashimi plots

    Draws Sashimi plots (pdf files) for all results below FDR threshold
    from all five rMATS events


    Parameters
    ----------
    infiles: list
        list of results files from rMATS

    MATS_fdr : string
       :term:`PARAMS`. User specified threshold for result drawing

    outfile: string
        summary file containing number of results below FDR threshold
    '''

    infile, design = infiles
    fdr = PARAMS["MATS_fdr"]
    if not os.path.exists(outfile):
        os.makedirs(outfile)

    PipelineSplicing.rmats2sashimi(infile, design, fdr, outfile)


###################################################################
###################################################################
###################################################################
# Pipeline management
###################################################################

@follows(loadMATS,
         loadCollateMATS,
         loadPermuteMATS,
         runSashimi,
         runDEXSeq)
def full():
    pass


@follows(mkdir("report"))
def build_report():
    '''build report from scratch.

    Any existing report will be overwritten.
    Currently report function is not active for this pipeline
    '''

    E.info("starting report build process from scratch")
    P.run_report(clean=True)


@follows(mkdir("report"))
def update_report():
    '''update report.

    This will update a report with any changes inside the report
    document or code. Note that updates to the data will not cause
    relevant sections to be updated. Use the cgatreport-clean utility
    first.
    '''

    E.info("updating report")
    P.run_report(clean=False)


@follows(update_report)
def publish_report():
    '''publish report in the CGAT downloads directory.'''

    E.info("publishing report")
    P.publish_report()


def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)


if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
