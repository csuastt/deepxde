import itertools

import numpy as np
from scipy import stats
from sklearn import preprocessing

from .geometry import Geometry, Literal, Union
from .sampler import sample
from .. import config
from .. import backend as bkd


class Hypercube(Geometry):
    def __init__(self, xmin, xmax):
        if len(xmin) != len(xmax):
            raise ValueError("Dimensions of xmin and xmax do not match.")

        self.xmin = np.array(xmin, dtype=config.real(np))
        self.xmax = np.array(xmax, dtype=config.real(np))
        if np.any(self.xmin >= self.xmax):
            raise ValueError("xmin >= xmax")

        self.side_length = self.xmax - self.xmin
        super().__init__(
            len(xmin), (self.xmin, self.xmax), np.linalg.norm(self.side_length)
        )
        self.volume = np.prod(self.side_length)

    def inside(self, x):
        return np.logical_and(
            np.all(x >= self.xmin, axis=-1), np.all(x <= self.xmax, axis=-1)
        )

    def on_boundary(self, x):
        _on_boundary = np.logical_or(
            np.any(np.isclose(x, self.xmin), axis=-1),
            np.any(np.isclose(x, self.xmax), axis=-1),
        )
        return np.logical_and(self.inside(x), _on_boundary)

    def boundary_normal(self, x):
        _n = -np.isclose(x, self.xmin).astype(config.real(np)) + np.isclose(
            x, self.xmax
        )
        # For vertices, the normal is averaged for all directions
        idx = np.count_nonzero(_n, axis=-1) > 1
        if np.any(idx):
            print(
                f"Warning: {self.__class__.__name__} boundary_normal called on vertices. "
                "You may use PDE(..., exclusions=...) to exclude the vertices."
            )
            l = np.linalg.norm(_n[idx], axis=-1, keepdims=True)
            _n[idx] /= l
        return _n

    def uniform_points(self, n, boundary=True):
        dx = (self.volume / n) ** (1 / self.dim)
        xi = []
        for i in range(self.dim):
            ni = int(np.ceil(self.side_length[i] / dx))
            if boundary:
                xi.append(
                    np.linspace(
                        self.xmin[i], self.xmax[i], num=ni, dtype=config.real(np)
                    )
                )
            else:
                xi.append(
                    np.linspace(
                        self.xmin[i],
                        self.xmax[i],
                        num=ni + 1,
                        endpoint=False,
                        dtype=config.real(np),
                    )[1:]
                )
        x = np.array(list(itertools.product(*xi)))
        if n != len(x):
            print(
                "Warning: {} points required, but {} points sampled.".format(n, len(x))
            )
        return x

    def random_points(self, n, random="pseudo"):
        x = sample(n, self.dim, random)
        return (self.xmax - self.xmin) * x + self.xmin

    def random_boundary_points(self, n, random="pseudo"):
        x = sample(n, self.dim, random)
        # Randomly pick a dimension
        rand_dim = np.random.randint(self.dim, size=n)
        # Replace value of the randomly picked dimension with the nearest boundary value (0 or 1)
        x[np.arange(n), rand_dim] = np.round(x[np.arange(n), rand_dim])
        return (self.xmax - self.xmin) * x + self.xmin

    def periodic_point(self, x, component):
        y = np.copy(x)
        _on_xmin = np.isclose(y[:, component], self.xmin[component])
        _on_xmax = np.isclose(y[:, component], self.xmax[component])
        y[:, component][_on_xmin] = self.xmax[component]
        y[:, component][_on_xmax] = self.xmin[component]
        return y

    def approxdist2boundary(self, x, 
            where: None = None, 
            smoothness: None = None):
        """Do not use this overload. 
        Use the overload with `inside` argument instead."""
        raise NotImplementedError(
            "Do not use this overload. Use the overload with `inside` argument instead."
        )

    def approxdist2boundary(self, x, 
        where: None = None, 
        smoothness: Literal["L", "M", "H"] = "M",
        inside: bool = True):
        """
        `inside`: `x` is either inside or outside the geometry.
        The cases that there are both points inside and points
        outside the geometry are NOT allowed.

        NOTE: currently only support `inside=True`.

        WARNING: in current implementation, 
        numerical underflow may happen for high dimensionalities
        when `smoothness="M"` or `smoothness="H"`. 

        See `Geometry.approxdist2boundary()` for more info on args.
        """
        
        assert where is None, "where!=None is not supported for Hypercube"
        assert smoothness in ["L", "M", "H"], "smoothness must be one of L, M, H"
        assert self.dim >= 2
        assert inside, "inside=False is not supported for Hypercube"

        if not hasattr(self, "self.xmin_tensor"):
            self.xmin_tensor = bkd.as_tensor(self.xmin)
            self.xmax_tensor = bkd.as_tensor(self.xmax)

        dist_l = bkd.abs((x - self.xmin_tensor) /
                        (self.xmax_tensor - self.xmin_tensor) * 2)
        dist_r = bkd.abs((x - self.xmax_tensor) /
                        (self.xmax_tensor - self.xmin_tensor) * 2)
        if smoothness == "L":
            dist_l = bkd.min(dist_l, dim=-1, keepdims=True)
            dist_r = bkd.min(dist_r, dim=-1, keepdims=True)
            return bkd.minimum(dist_l, dist_r)
        else:
            # TODO: fix potential numerical underflow
            dist_l = bkd.prod(dist_l, dim=-1, keepdims=True)
            dist_r = bkd.prod(dist_r, dim=-1, keepdims=True)
            return dist_l * dist_r


class Hypersphere(Geometry):
    def __init__(self, center, radius):
        self.center = np.array(center, dtype=config.real(np))
        self.radius = radius
        super().__init__(
            len(center), (self.center - radius, self.center + radius), 2 * radius
        )

        self._r2 = radius ** 2

    def inside(self, x):
        return np.linalg.norm(x - self.center, axis=-1) <= self.radius

    def on_boundary(self, x):
        return np.isclose(np.linalg.norm(x - self.center, axis=-1), self.radius)

    def distance2boundary_unitdirn(self, x, dirn):
        # https://en.wikipedia.org/wiki/Line%E2%80%93sphere_intersection
        xc = x - self.center
        ad = np.dot(xc, dirn)
        return (-ad + (ad ** 2 - np.sum(xc * xc, axis=-1) + self._r2) ** 0.5).astype(config.real(np))

    def distance2boundary(self, x, dirn):
        return self.distance2boundary_unitdirn(x, dirn / np.linalg.norm(dirn))

    def mindist2boundary(self, x):
        return np.amin(self.radius - np.linalg.norm(x - self.center, axis=-1))

    def approxdist2boundary(self, x, 
        where: None = None, 
        smoothness: Literal["L", "M", "H"] = "M"):
        
        assert where is None, "where!=None is not supported for Hypersphere or its subclasses"
        assert smoothness in ["L", "M", "H"], "smoothness must be one of L, M, H"

        if not hasattr(self, "self.center_tensor"):
            self.center_tensor = bkd.as_tensor(self.center)
            self.radius_tensor = bkd.as_tensor(self.radius)

        diff = bkd.norm(
            x - self.center_tensor, axis=-1, keepdims=True) - self.radius
        if smoothness == "L" or smoothness == "M":
            return bkd.abs(diff)
        else:
            return bkd.square(diff)

    def boundary_normal(self, x):
        _n = x - self.center
        l = np.linalg.norm(_n, axis=-1, keepdims=True)
        _n = _n / l * np.isclose(l, self.radius)
        return _n

    def random_points(self, n, random="pseudo"):
        # https://math.stackexchange.com/questions/87230/picking-random-points-in-the-volume-of-sphere-with-uniform-probability
        if random == "pseudo":
            U = np.random.rand(n, 1).astype(config.real(np))
            X = np.random.normal(size=(n, self.dim)).astype(config.real(np))
        else:
            rng = sample(n, self.dim + 1, random)
            U, X = rng[:, 0:1], rng[:, 1:]  # Error if X = [0, 0, ...]
            X = stats.norm.ppf(X).astype(config.real(np))
        X = preprocessing.normalize(X)
        X = U ** (1 / self.dim) * X
        return self.radius * X + self.center

    def random_boundary_points(self, n, random="pseudo"):
        # http://mathworld.wolfram.com/HyperspherePointPicking.html
        if random == "pseudo":
            X = np.random.normal(size=(n, self.dim)).astype(config.real(np))
        else:
            U = sample(n, self.dim, random)  # Error for [0, 0, ...] or [0.5, 0.5, ...]
            X = stats.norm.ppf(U).astype(config.real(np))
        X = preprocessing.normalize(X)
        return self.radius * X + self.center

    def background_points(self, x, dirn, dist2npt, shift):
        dirn = dirn / np.linalg.norm(dirn)
        dx = self.distance2boundary_unitdirn(x, -dirn)
        n = max(dist2npt(dx), 1)
        h = dx / n
        pts = x - np.arange(-shift, n - shift + 1, dtype=config.real(np))[:, None] * h * dirn
        return pts
