import os
import argparse
from collections import defaultdict

from ragoo_utilities.utilities import log, run
from ragoo_utilities.Aligner import Minimap2Aligner
from ragoo_utilities.Aligner import NucmerAligner
from ragoo_utilities.AlignmentReader import AlignmentReader
from ragoo_utilities.ContigAlignment import ContigAlignment


def write_orderings(ordering_dict, ctg_dict, gap_dict, overwrite, out_path):
    out_file = out_path + "orderings.bed"

    # Check if the output file already exists
    if os.path.isfile(out_file):
        if not overwrite:
            log("retaining pre-existing file: " + out_file)
            return

    # Proceed with writing the intermediate output
    gap_id = 0
    all_out_lines = []
    for ref_header in ordering_dict:
        pos = 0
        new_ref_header = ref_header + "_RaGOO"
        q_seqs = ordering_dict[ref_header]
        gap_seqs = gap_dict[ref_header]

        # Iterate through the query sequences for this reference header
        # TODO give each gap a unique ID where query seq header goes
        for i in range(len(q_seqs)):
            out_line = []
            q = q_seqs[i][2]
            qlen = ctg_dict[q].query_len
            strand = ctg_dict[q].orientation
            gc, lc, oc = ctg_dict[q].grouping_confidence, ctg_dict[q].location_confidence, ctg_dict[q].orientation_confidence
            out_line.append(new_ref_header)
            out_line.append(str(pos))
            pos += qlen
            out_line.append(str(pos) + "\ts")
            out_line.append(q)
            out_line.append(strand)
            out_line.append(str(gc))
            out_line.append(str(lc))
            out_line.append(str(oc))
            all_out_lines.append("\t".join(out_line))

            if i < len(gap_seqs):
                out_line = []
                # Print the gap line
                out_line.append(new_ref_header)
                out_line.append(str(pos))
                pos += gap_seqs[i]
                out_line.append(str(pos) + "\tg\t" + str(gap_id))
                gap_id += 1
                out_line.append("NA\tNA\tNA\tNA")
                all_out_lines.append("\t".join(out_line))

    log("writing: " + out_file)
    with open(out_file, "w") as f:
        f.write("\n".join(all_out_lines))


def main():
    parser = argparse.ArgumentParser(description='Scaffold contigs according to alignments to a reference (v2.0.0)')
    parser.add_argument("reference", metavar="<reference.fasta>", type=str, help="reference fasta file. must not be gzipped.")
    parser.add_argument("query", metavar="<query.fasta>", type=str, help="query fasta file to be scaffolded. must not be gzipped.")
    parser.add_argument("-o", metavar="STR", type=str, default="ragoo_output", help="output directory name [ragoo_output]")
    parser.add_argument("--aligner", metavar="PATH", type=str, default="minimap2", help="Aligner ('nucmer' or 'minimap2') to use for scaffolding. PATHs allowed [minimap2]")
    parser.add_argument("--mm2-params", metavar="STR", type=str, default="-k19 -w19 -t1", help="Space delimted parameters to pass directly to minimap2 ['-k19 -w19 -t1']")
    parser.add_argument("--nucmer-params", metavar="STR", type=str, default="-l 100 -c 500", help="Space delimted parameters to pass directly to nucmer ['-l 100 -c 500']")
    parser.add_argument("-e", metavar="<exclude.txt>", type=str, default="", help="single column text file of reference headers to ignore")
    parser.add_argument("-j", metavar="<skip.txt>", type=str, default="", help="List of contigs to automatically leave unplaced")
    parser.add_argument("-g", metavar="INT", type=int, default=100, help="gap size for padding in pseudomolecules [100]")
    parser.add_argument("-l", metavar="INT", type=int, default=10000, help="minimum unique alignment length to use for scaffolding [10000]")
    parser.add_argument("-q", metavar="INT", type=int, default=0, help="minimum mapping quality value for alignments. only pertains to minimap2 alignments [0]")
    parser.add_argument("-i", metavar="FLOAT", type=float, default=0.2, help="minimum grouping confidence score needed to be localized [0.2]")
    parser.add_argument("-a", metavar="FLOAT", type=float, default=0.0, help="minimum location confidence score needed to be localized [0.0]")
    parser.add_argument("-d", metavar="FLOAT", type=float, default=0.0, help="minimum orientation confidence score needed to be localized [0.0]")
    parser.add_argument("-C", action='store_true', default=False, help="write unplaced contigs individually instead of making a chr0")
    parser.add_argument("-r", action='store_true', default=False, help="infer gap pad sizes from the reference. '-g' is used when adjacent contigs overlap")
    parser.add_argument("-w", action='store_true', default=False, help="overwrite pre-existing intermediate files. ragoo.fasta will always be overwritten")

    # Get the command line arguments and ensure all paths are absolute.
    args = parser.parse_args()
    reference_file = os.path.abspath(args.reference)
    query_file = os.path.abspath(args.query)
    output_path = args.o.replace("/", "").replace(".", "")
    min_len = args.l
    min_q = args.q
    gap_size = args.g
    group_score_thresh = args.i
    loc_score_thresh = args.a
    orient_score_thresh = args.d
    make_chr0 = not args.C
    infer_gaps = args.r
    overwrite_files = args.w

    skip_file = args.j
    if skip_file:
        skip_file = os.path.abspath(args.j)

    exclude_file = args.e
    if exclude_file:
        exclude_file = os.path.abspath(args.e)

    # Get aligner arguments
    aligner_path = args.aligner
    aligner = aligner_path.split("/")[-1]
    if aligner.split("/")[-1] not in {'minimap2', 'nucmer'}:
        raise ValueError("Must specify either 'minimap2' or 'nucmer' (PATHs allowed) with '--aligner'.")
    mm2_params = args.mm2_params
    nucmer_params = args.nucmer_params

    # Get the skip and exclude sets
    query_blacklist = set()
    if skip_file:
        with open(skip_file, "r") as f:
            for line in f:
                query_blacklist.add(line.rstrip())

    ref_blacklist = set()
    if exclude_file:
        with open(exclude_file, "r") as f:
            for line in f:
                ref_blacklist.add(line.rstrip())

    # Get the current working directory and output path
    cwd = os.getcwd()
    output_path = cwd + "/" + output_path + "/"
    if not os.path.exists(output_path):
        os.mkdir(output_path)

    # Align the query to the reference
    log("Aligning the query to the reference")
    if aligner == "minimap2":
        al = Minimap2Aligner(reference_file, query_file, aligner_path, mm2_params, output_path + "query_against_ref", in_overwrite=overwrite_files)
    else:
        al = NucmerAligner(reference_file, query_file, aligner_path, nucmer_params, output_path + "query_against_ref", in_overwrite=overwrite_files)
    al.run_aligner()

    # Read and organize the alignments
    log('Reading alignments')
    ctg_alns = dict()
    aln_reader = AlignmentReader(output_path + "query_against_ref", aligner)
    for aln_line in aln_reader.parse_alignments():

        # Check that the contig and reference in this alignment are allowed.
        if aln_line.query_header not in query_blacklist and aln_line.ref_header not in ref_blacklist:
            if aln_line.query_header not in ctg_alns:
                ctg_alns[aln_line.query_header] = ContigAlignment(aln_line.query_header, aln_line.query_len, [aln_line.ref_header], [aln_line.ref_len], [aln_line.ref_start], [aln_line.ref_end], [aln_line.query_start], [aln_line.query_end], [aln_line.strand], [aln_line.mapq])
            else:
                ctg_alns[aln_line.query_header] = ctg_alns[aln_line.query_header].add_alignment(aln_line.ref_header, aln_line.ref_len, aln_line.ref_start, aln_line.ref_end, aln_line.query_start, aln_line.query_end, aln_line.strand, aln_line.mapq)

    # Filter the alignments
    log("Filtering alignments")
    if aligner == "minimap2":
        log('Alignments are from minimap2. removing alignments with mapq < %r.' % min_q)
        for i in ctg_alns:
            ctg_alns[i] = ctg_alns[i].filter_mapq(min_q)
            if ctg_alns[i] is not None:
                ctg_alns[i] = ctg_alns[i].unique_anchor_filter(min_len)
    else:
        for i in ctg_alns:
            ctg_alns[i] = ctg_alns[i].unique_anchor_filter(min_len)

    # Remove query sequences which have no more qualifying alignments
    fltrd_ctg_alns = dict()
    for i in ctg_alns:
        if ctg_alns[i] is not None:
            if all([
                ctg_alns[i].grouping_confidence > group_score_thresh,
                ctg_alns[i].location_confidence > loc_score_thresh,
                ctg_alns[i].orientation_confidence > orient_score_thresh
            ]):
                fltrd_ctg_alns[i] = ctg_alns[i]

    # For each reference sequence which has at least one assigned query sequence, get the list of
    # all query sequences assigned to that reference sequence.
    log("Ordering and orienting query sequences")
    mapped_ref_seqs = defaultdict(list)
    for i in fltrd_ctg_alns:
        best_ref = fltrd_ctg_alns[i].best_ref_header
        ref_start, ref_end = fltrd_ctg_alns[i].get_best_ref_pos()
        mapped_ref_seqs[best_ref].append((ref_start, ref_end, i))

    # Sort the query sequences for each reference sequence and define the padding sizes between adjacent query seqs
    pads_sizes = dict()
    for i in mapped_ref_seqs:
        mapped_ref_seqs[i] = sorted(mapped_ref_seqs[i])
        if infer_gaps:
            # Infer the gap sizes between adjacent query seqs
            pads_sizes[i] = []
            for j in range(1, len(mapped_ref_seqs[i])):
                left_ctg = mapped_ref_seqs[i][j-1][2]
                left_min, left_max = fltrd_ctg_alns[left_ctg].get_best_ref_flanks()
                right_ctg = mapped_ref_seqs[i][j][2]
                right_min, right_max = fltrd_ctg_alns[right_ctg].get_best_ref_flanks()

                # If the contigs overlap, revert to the fixed pre-defined gap size
                if right_min - left_max >= 0:
                    pads_sizes[i].append(right_min - left_max)
                else:
                    pads_sizes[i].append(gap_size)
        else:
            pads_sizes[i] = [gap_size for i in range(len(mapped_ref_seqs[i])-1)]

    # Write the intermediate output file
    write_orderings(mapped_ref_seqs, fltrd_ctg_alns, pads_sizes, overwrite_files, output_path)

    # Write the scaffolds
    log("Writing scaffolds")
    if make_chr0:
        cmd = [
            "python3",
            "build_scaffolds.py",
            output_path + "orderings.bed",
            query_file,
            output_path + "ragoo.fasta",
            str(gap_size)
        ]
    else:
        cmd = [
            "python3",
            "build_scaffolds.py",
            "-C",
            output_path + "orderings.bed",
            query_file,
            output_path + "ragoo.fasta",
            str(gap_size)
        ]
    run(" ".join(cmd))


if __name__ == "__main__":
    main()
