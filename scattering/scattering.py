import itertools as it
from progressbar import ProgressBar

import mdtraj as md
import numpy as np
from scipy.integrate import simps

from scattering.utils.utils import rdf_by_frame
from scattering.utils.utils import get_dt
from scattering.utils.constants import get_form_factor


# __all__ = ['structure_factor', 'compute_partial_van_hove', 'compute_van_hove']


def find_element(name, top):
    """Find the element symbol given the name of the atom and the top file."""
    for atom in top.atoms:
        if atom.name == name:
            return atom.element.symbol


def find_atomic_number(name, top):
    """Find the atomic number given the name of the atom and the top file."""
    for atom in top.atoms:
        if atom.name == name:
            return atom.element.number


class Atominfo:
    """Class to save the properties of an atom."""
    def __init__(self, name, element, atomic_number):
        self.name = name
        self.symbol = element
        self.atomic_number = atomic_number


def structure_factor(
    trj,
    Q_range=(0.5, 50),
    n_points=1000,
    framewise_rdf=False,
    weighting_factor="fz",
    form="atomic",
    partial=False,
):
    """Compute the structure factor through a fourier transform of
    the radial distribution function.

    The considered trajectory must include valid elements.

    The computed structure factor is only valid for certain values of Q. The
    lowest value of Q that can sufficiently be described by a box of
    characteristic length `L` is `2 * pi / (L / 2)`.

    Parameters
    ----------
    trj : mdtraj.Trajectory
        A trajectory for which the structure factor is to be computed.
    Q_range : list or np.ndarray, default=(0.5, 50)
        Minimum and maximum Values of the scattering vector, in `1/nm`, to be
        consdered.
    n_points : int, default=1000
    framewise_rdf : boolean, default=False
        If True, computes the rdf frame-by-frame. This can be useful for
        managing memory in large systems.
    weighting_factor : string, optional, default='fz'
        Weighting factor for calculating the structure-factor, default is Faber-Ziman.
        See https://openscholarship.wustl.edu/etd/1358/ and http://isaacs.sourceforge.net/manual/page26_mn.html for details.
    form : string, optional, default='atomic'
        Method for determining form factors. If default, form factors are estimated from
        atomic numbers.  If 'cromer-mann', form factors are determined from Cromer-Mann
        tables.
    partial : boolean, optional, default=False
        If true, return a dictionary of partial structure factors

    Returns
    -------
    Q : np.ndarray
        The values of the scattering vector, in `1/nm`, that was considered.
    S : np.ndarray
        The structure factor of the trajectory

    """
    if weighting_factor not in ["fz", "al"]:
        raise ValueError(
            "Invalid weighting_factor `{}` is given."
            "  The only weighting_factor currently supported is `fz`, and `al`.".format(
                weighting_factor
            )
        )

    rho = np.mean(trj.n_atoms / trj.unitcell_volumes)
    L = np.min(trj.unitcell_lengths)

    top = trj.topology
    unique_residues = []

    for a in top.residues:
        if a.name not in unique_residues:
            unique_residues.append(a.name)
            residue_atoms = []
            for b in a.atoms:
                residue_atoms.append(b.name)
            print(
                "The residue name is {} and it contains {}".format(
                    a.name, residue_atoms
                )
            )

    elements = set([a.element for a in top.atoms])
    names = set([a.name for a in top.atoms])
    compositions = dict()
    sq = dict()

    Q = np.logspace(np.log10(Q_range[0]), np.log10(Q_range[1]), num=n_points)
    S = np.zeros(shape=(len(Q)))

    for name in names:
        compositions[name] = len(top.select("name {}".format(name))) / trj.n_atoms

    # Compute partial structure factors
    print("Computing structure factors ...")

    atoms = set()
    for name in names:
        element = find_element(name, top)
        atomic_number = find_atomic_number(name, top)
        atoms.add(Atominfo(name, element, atomic_number))

    for (atom1, atom2) in it.product(atoms, repeat=2):
        name1 = atom1.name
        name2 = atom2.name

        sq["{0}{1}".format(name1, name2)] = partial_structure_factor(
            trj=trj,
            selection1=f"name {name1}",
            selection2=f"name {name2}",
            Q_range=Q_range,
            L=L,
            n_points=n_points,
            framewise_rdf=framewise_rdf,
        )[1]
    if partial:
        norm_sq = dict()
    print("Computing normalization ... ")
    for i, q in enumerate(Q):
        num = 0
        denom = 0

        for atom in atoms:
            denom += _get_normalize(
                method=weighting_factor,
                c=compositions[atom.name],
                f=get_form_factor(atom.symbol, q=q / 10, method=form),
            )

        if weighting_factor == "fz":
            denom = denom ** 2

        for (atom1, atom2) in it.product(atoms, repeat=2):
            e1 = atom1.symbol
            e2 = atom2.symbol
            name1 = atom1.name
            name2 = atom2.name
            f_a = get_form_factor(e1, q=q / 10, method=form)
            f_b = get_form_factor(e2, q=q / 10, method=form)

            x_a = compositions[name1]
            x_b = compositions[name2]

            integral = sq[f"{name1}{name2}"][i]

            coefficient = x_a * x_b * f_a * f_b
            pre_factor = 4 * np.pi * rho

            partial_sq = integral * pre_factor
            num += coefficient * (partial_sq)

            if partial:
                try:
                    norm_sq[(name1, name2)][i] = (partial_sq * coefficient) / denom
                except:
                    norm_sq[(name1, name2)] = np.zeros((len(Q)))
                    norm_sq[(name1, name2)][i] = (partial_sq * coefficient) / denom

        S[i] = num / denom

    if partial:
        return norm_sq
    else:
        return Q, S


def partial_structure_factor(
    trj,
    selection1,
    selection2,
    Q_range=(0.5, 50),
    L=None,
    n_points=1000,
    framewise_rdf=False,
):
    """Compute the structure factor between a pair of atoms

    The considered trajectory must include valid elements.

    The computed structure factor is only valid for certain values of Q. The
    lowest value of Q that can sufficiently be described by a box of
    characteristic length `L` is `2 * pi / (L / 2)`.

    Parameters
    ----------
    trj : mdtraj.Trajectory
        A trajectory for which the structure factor is to be computed.
    selection1 : str
        selection to be considered, in the style of MDTraj atom selection
    selection2 : str
        selection to be considered, in the style of MDTraj atom selection
    Q_range : list or np.ndarray, default=(0.5, 50)
        Minimum and maximum Values of the scattering vector, in `1/nm`, to be
        consdered.
    L : float, optional, default=None
        Unitcell length of chemical system, opt. If None, set to np.min(trj.unitcell_lengths)
    n_points : int, default=1000
    framewise_rdf : boolean, default=False
        If True, computes the rdf frame-by-frame. This can be useful for
        managing memory in large systems.

    Returns
    -------
    Q : np.ndarray
        The values of the scattering vector, in `1/nm`, that was considered.
    S : np.ndarray
        The structure factor of the trajectory

    """
    if not L:
        L = np.min(trj.unitcell_lengths)

    Q = np.logspace(np.log10(Q_range[0]), np.log10(Q_range[1]), num=n_points)

    pairs = trj.top.select_pairs(
        selection1=selection1,
        selection2=selection2,
    )

    if framewise_rdf:
        r, g_r = rdf_by_frame(trj, pairs=pairs, r_range=(0, L / 2), bin_width=0.001)
    else:
        r, g_r = md.compute_rdf(trj, pairs=pairs, r_range=(0, L / 2), bin_width=0.001)

    S = np.zeros((len(Q)))
    for i, q in enumerate(Q):
        # Fourier transform of g(r)
        integral = simps(r ** 2 * (g_r - 1) * np.sin(q * r) / (q * r), r)
        S[i] = integral

    return Q, S


def compute_dynamic_rdf(trj):
    """Compute r_ij(t), the distance between atom j at time t and atom i and
    time 0. Note that this alone is likely useless, but is an intermediate
    variable in the construction of a dynamic structure factor.
    See 10.1103/PhysRevE.59.623.

    Parameters
    ----------
    trj : mdtraj.Trajectory
        A trajectory for which the structure factor is to be computed

    Returns
    -------
    r_ij : np.ndarray, shape=(trj.n_atoms, trj.n_atoms, trj.n_frames)
        A three-dimensional array of interatomic distances
    """

    n_atoms = trj.n_atoms
    n_frames = trj.n_frames

    r_ij = np.ndarray(shape=(trj.n_atoms, trj.n_atoms, trj.n_frames))

    for n_frame, frame in enumerate(trj):
        for atom_i in range(trj.n_atoms):
            for atom_j in range(trj.n_atoms):
                r_ij[atom_i, atom_j, n_frame] = compute_distance(
                    trj.xyz[n_frame, atom_j], trj.xyz[0, atom_i]
                )

    return r_ij


def compute_distance(point1, point2):
    return np.sqrt(np.sum((point1 - point2) ** 2))


def compute_rdf_from_partial(trj, r_range=None):
    compositions = dict()
    form_factors = dict()
    rdfs = dict()

    L = np.min(trj.unitcell_lengths)
    top = trj.topology
    elements = set([a.element for a in top.atoms])

    denom = 0
    for elem in elements:
        compositions[elem.symbol] = (
            len(top.select("element {}".format(elem.symbol))) / trj.n_atoms
        )
        form_factors[elem.symbol] = elem.atomic_number
        denom += compositions[elem.symbol] * form_factors[elem.symbol]
    for i, (elem1, elem2) in enumerate(it.product(elements, repeat=2)):
        e1 = elem1.symbol
        e2 = elem2.symbol

        x_a = compositions[e1]
        x_b = compositions[e2]

        f_a = form_factors[e1]
        f_b = form_factors[e2]

        try:
            g_r = rdfs["{0}{1}".format(e1, e2)]
        except KeyError:
            pairs = top.select_pairs(
                selection1="element {}".format(e1), selection2="element {}".format(e2)
            )
            if r_range == None:
                r, g_r = md.compute_rdf(trj, pairs=pairs, r_range=(0, L / 2))
            else:
                r, g_r = md.compute_rdf(trj, pairs=pairs, r_range=r_range)
            rdfs["{0}{1}".format(e1, e2)] = g_r
        if i == 0:
            total = g_r * (x_a * x_b * f_a * f_b) / denom ** 2
        else:
            total += g_r * (x_a * x_b * f_a * f_b) / denom ** 2

    return r, total


def _get_normalize(method, c, f):
    """Get normalization factor"""
    if method == "fz":
        denom = c * f
        return denom
    elif method == "al":
        denom = c * (f ** 2)
        return denom
