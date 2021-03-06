#!/usr/bin/env python

import datetime
import os
import subprocess
import sys
from os.path import join, isdir, exists, abspath, realpath, dirname

import click

import config
from scripts import polishing
from scripts.assembly import Assembly
from scripts.utils import get_fasta_len, check_fasta_files


def set_params(fnames, threads):
    if not check_fasta_files(fnames):
        sys.exit(1)
    assembly_len = max([get_fasta_len(f) for f in fnames])
    #print("Max assembly len: %d" % assembly_len)
    config.KMER_WINDOW_SIZE = max(500, assembly_len//150)
    if assembly_len < 100000:
        config.BP_WINDOW_SIZE = 200
    elif assembly_len < 1000000:
        config.BP_WINDOW_SIZE = 500
    else:
        config.BP_WINDOW_SIZE = 1000
    config.MOVING_AVG_WINDOW_SIZE = min(20, max(20, assembly_len//config.BP_WINDOW_SIZE//20))
    config.MAX_THREADS = threads


@click.command()
@click.argument('assembly_fnames', type=click.Path(exists=True), nargs=-1)
@click.option('--nano', 'nano_reads_fname', type=click.Path(exists=True), help='File with ONT reads')
@click.option('--pacbio', 'pacbio_reads_fname', type=click.Path(exists=True), help='File with PacBio CLR reads')
@click.option('-o', 'out_dir',  type=click.Path(), required=True, help='Output folder')
@click.option('-t', 'threads', type=click.INT, help='Threads', default=config.MAX_THREADS)
@click.option('-m', 'monomers_fname', type=click.Path(), help='Monomer sequence')
@click.option('-l', 'labels', help='Comma separated list of assembly labels')
@click.option('--hifi', 'hifi_reads_fname',  type=click.Path(), help='File with PacBio HiFi reads')
@click.option('--only-polish', 'only_polish', is_flag=True, help='Run polishing only')
@click.option('--no-nucl-align', 'no_nucl_alignment', is_flag=True, help='Do not perform nucleotide alignment '
                                                                         '(use with caution)')
@click.option('-f', '--no-reuse', 'no_reuse', is_flag=True, help='Do not reuse old files')
def main(assembly_fnames, labels, nano_reads_fname, pacbio_reads_fname, hifi_reads_fname, out_dir, threads, monomers_fname,
         no_reuse, only_polish, no_nucl_alignment):
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("%s TandemQUAST started" % date)

    if nano_reads_fname and pacbio_reads_fname or (not nano_reads_fname and not pacbio_reads_fname):
        print("ERROR! You should specify ONE path to a file with reads (ONT or Pacbio CLR reads)")
        sys.exit(2)

    if nano_reads_fname:
        reads_fname = nano_reads_fname
        config.platform = "nano"
    else:
        reads_fname = pacbio_reads_fname
        config.platfrom = "pacbio"

    if not assembly_fnames:
        print("ERROR! You should specify at least one assembly file.")
        sys.exit(2)

    set_params(assembly_fnames, threads)

    out_dir = abspath(out_dir)
    report_dir = join(out_dir, "report")
    if not isdir(report_dir):
        os.makedirs(report_dir)
    tmp_dir = join(out_dir, "tmp")
    if not isdir(tmp_dir):
        os.makedirs(tmp_dir)

    # -----MASK SEQUENCE----
    list_labels = [None] * len(assembly_fnames)
    if labels:
        list_labels = labels.replace('"', '').split(',')
        if len(list_labels) != len(assembly_fnames):
            print("ERROR! Number of labels must correspond to the number of analyzed assemblies")
            sys.exit(2)

    assemblies = [Assembly(assembly_fnames[i], name=list_labels[i], out_dir=out_dir) for i in range(len(assembly_fnames))]
    # -----SELECT KMERS----
    from scripts import coverage_test, bp_analysis, kmer_analysis, pairwise_comparison, discordance, monomer_analysis

    if only_polish:
        polishing.do(assemblies, reads_fname, hifi_reads_fname, out_dir, tmp_dir)
        date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("")
        print("%s TandemQUAST finished" % date)
        sys.exit(0)

    # -----MAPPING----
    print("")
    print("*********************************")
    print("Running TandemMapper...")
    cmdl = [abspath(join(dirname(realpath(__file__)), "tandemmapper.py")), "-t", str(threads), "-o", out_dir] + \
           ["--nano" if nano_reads_fname else "--pacbio", reads_fname] + \
           ["-l", ",".join([assembly.label for assembly in assemblies])] + \
           (["--no-nucl-align"] if no_nucl_alignment else []) + \
           [assembly.fname for assembly in assemblies] + (["-f"] if no_reuse else [])
    return_code = subprocess.call(cmdl)
    if return_code != 0:
        print("ERROR: tandemMapper failed! Please check input files and tandemMapper output. TandemQUAST cannot proceed without alignments")
        sys.exit(2)

    # -----COVERAGE----
    coverage_test.do(assemblies, out_dir)

    # -----BREAKPOINTS----
    bp_analysis.do(assemblies, out_dir)

    if not no_nucl_alignment:
        # -----KMER-based ANALYSIS----
        kmer_analysis.do(assemblies, reads_fname, out_dir, no_reuse)

    # -----PAIRWISE----
    pairwise_comparison.do(assemblies, out_dir)
    discordance.do(assemblies, reads_fname, out_dir)

    if monomers_fname and "linux" not in sys.platform:
        print("ERROR: StringDecomposer can be run on Linux only. Monomer-based metrics will not be calculated")
        monomers_fname = None

    # -----FINISH FOR TANDEM REPEATS----
    if not monomers_fname or not exists(monomers_fname):
        date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("")
        print("%s TandemQUAST finished" % date)
        sys.exit(0)

    # -----MONOMER AND UNIT ANALYSIS----
    monomer_analysis.do(assemblies, reads_fname, monomers_fname, out_dir)

    #make_full_report(assemblies, out_dir)

    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("")
    print("%s TandemQUAST finished. Reports for different metrics are saved to %s" % (date, report_dir))


if __name__ == '__main__':
    main()