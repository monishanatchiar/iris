import numpy as np

import iris


def _3d_xyz_from_latlon(lon, lat):
    """
    Return locations of (lon, lat) in 3D space.

    Args:

    * lon, lat: (arrays in degrees)

    Returns:

        xyz : (array, dtype=float64)
            cartesian coordinates on a unit sphere.  Dimension 0 maps x,y,z.

    """
    lon1 = np.deg2rad(lon).astype(np.float64)
    lat1 = np.deg2rad(lat).astype(np.float64)

    x = np.cos(lat1) * np.cos(lon1)
    y = np.cos(lat1) * np.sin(lon1)
    z = np.sin(lat1)

    result = np.concatenate([array[np.newaxis] for array in (x, y, z)])

    return result


def _latlon_from_xyz(xyz):
    """
    Return arrays of lons+lats angles from xyz locations.

    Args:

    * xyz: (array)
        positions array, of dims (3, <others>), where index 0 maps x/y/z.

    Returns:

        lonlat : (array)
            spherical angles, of dims (2, <others>), in radians.
            Dim 0 maps longitude, latitude.

    """
    lons = np.arctan2(xyz[1], xyz[0])
    axial_radii = np.sqrt(xyz[0] * xyz[0] + xyz[1] * xyz[1])
    lats = np.arctan2(xyz[2], axial_radii)
    return np.array([lons, lats])


def _angle(p, q, r):
    """
    Return angle (in _radians_) of grid wrt local east.
    Anticlockwise +ve, as usual.
    {P, Q, R} are consecutive points in the same row,
    eg {v(i,j),f(i,j),v(i+1,j)}, or {T(i-1,j),T(i,j),T(i+1,j)}
    Calculate dot product of PR with lambda_hat at Q.
    This gives us cos(required angle).
    Disciminate between +/- angles by comparing latitudes of P and R.
    p, q, r, are all 2-element arrays [lon, lat] of angles in degrees.

    """
#    old_style = True
    old_style = False
    if old_style:
        mid_lons = np.deg2rad(q[0])

        pr =  _3d_xyz_from_latlon(r[0], r[1]) - _3d_xyz_from_latlon(p[0], p[1])
        pr_norm = np.sqrt(np.sum(pr**2, axis=0))
        pr_top = pr[1] * np.cos(mid_lons) - pr[0] * np.sin(mid_lons)

        index = pr_norm == 0
        pr_norm[index] = 1

        cosine = np.maximum(np.minimum(pr_top / pr_norm, 1), -1)
        cosine[index] = 0

        psi = np.arccos(cosine) * np.sign(r[1] - p[1])
        psi[index] = np.nan
    else:
        # Calculate unit vectors.
        midpt_lons, midpt_lats = q[0], q[1]
        lmb_r, phi_r = (np.deg2rad(arr) for arr in (midpt_lons, midpt_lats))
        phi_hatvec_x = -np.sin(phi_r) * np.cos(lmb_r)
        phi_hatvec_y = -np.sin(phi_r) * np.sin(lmb_r)
        phi_hatvec_z = np.cos(phi_r)
        shape_xyz = (1,) + midpt_lons.shape
        phi_hatvec = np.concatenate([arr.reshape(shape_xyz)
                                     for arr in (phi_hatvec_x,
                                                 phi_hatvec_y,
                                                 phi_hatvec_z)])
        lmb_hatvec_z = np.zeros(midpt_lons.shape)
        lmb_hatvec_y = np.cos(lmb_r)
        lmb_hatvec_x = -np.sin(lmb_r)
        lmb_hatvec = np.concatenate([arr.reshape(shape_xyz)
                                     for arr in (lmb_hatvec_x,
                                                 lmb_hatvec_y,
                                                 lmb_hatvec_z)])

        pr =  _3d_xyz_from_latlon(r[0], r[1]) - _3d_xyz_from_latlon(p[0], p[1])

        # Dot products to form true-northward / true-eastward projections.
        pr_cmpt_e = np.sum(pr * lmb_hatvec, axis=0)
        pr_cmpt_n = np.sum(pr * phi_hatvec, axis=0)
        psi = np.arctan2(pr_cmpt_n, pr_cmpt_e)

        # TEMPORARY CHECKS:
        # ensure that the two unit vectors are perpendicular.
        dotprod = np.sum(phi_hatvec * lmb_hatvec, axis=0)
        assert np.allclose(dotprod, 0.0)
        # ensure that the vector components carry the original magnitude.
        mag_orig = np.sum(pr * pr)
        mag_rot = np.sum(pr_cmpt_e * pr_cmpt_e) + np.sum(pr_cmpt_n * pr_cmpt_n)
        rtol = 1.e-3
        check = np.allclose(mag_rot, mag_orig, rtol=rtol)
        if not check:
            print (mag_rot, mag_orig)
            assert np.allclose(mag_rot, mag_orig, rtol=rtol)

    return psi


def gridcell_angles(x, y=None, cell_angle_boundpoints='mid-lhs, mid-rhs'):
    """
    Calculate gridcell orientation angles.

    Args:

    The inputs (x [,y]) can be any of the folliwing :

    * x (:class:`~iris.cube.Cube`):
        a grid cube with 2D longitude and latitude coordinates.

    * x, y (:class:`~iris.coords.Coord`):
        longitude and latitude coordinates.

    * x, y (2-dimensional arrays of same shape (ny, nx)):
        longitude and latitude cell center locations, in degrees.

    * x, y (3-dimensional arrays of same shape (ny, nx, 4)):
        longitude and latitude cell bounds, in degrees.
        The last index maps cell corners anticlockwise from bottom-left.

    Optional Args:

    * cell_angle_boundpoints (string):
        Controls which gridcell bounds locations are used to calculate angles,
        if the inputs are bounds or bounded coordinates.
        Valid values are 'lower-left, lower-right', which takes the angle from
        the lower left to the lower right corner, and 'mid-lhs, mid-rhs' which
        takes an angles between the average of the left-hand and right-hand
        pairs of corners.  The default is 'mid-lhs, mid-rhs'.

    Returns:

        angles : (2-dimensional cube)

            Cube of angles of grid-x vector from true Eastward direction for
            each gridcell, in radians.
            It also has longitude and latitude coordinates.  If coordinates
            were input the output has identical ones :  If the input was 2d
            arrays, the output coords have no bounds; or, if the input was 3d
            arrays, the output coords have bounds and centrepoints which are
            the average of the 4 bounds.

    """
    if hasattr(x, 'core_data'):
        # N.B. only "true" lats + longs will do : Cannot handle rotated !
        x, y = x.coord('longitude'), x.coord('latitude')

    # Now should have either 2 coords or 2 arrays.
    if not hasattr(x, 'shape') and hasattr(y, 'shape'):
        msg = ('Inputs (x,y) must have array shape property.'
               'Got type(x)={} and type(y)={}.')
        raise ValueError(msg.format(type(x), type(y)))

    x_coord, y_coord = None, None
    if isinstance(x, iris.coords.Coord) and isinstance(y, iris.coords.Coord):
        x_coord, y_coord = x.copy(), y.copy()
        x_coord.convert_units('degrees')
        y_coord.convert_units('degrees')
        if x_coord.ndim != 2 or y_coord.ndim != 2:
            msg = ('Coordinate inputs must have 2-dimensional shape. ',
                   'Got x-shape of {} and y-shape of {}.')
            raise ValueError(msg.format(x_coord.shape, y_coord.shape))
        if x_coord.shape != y_coord.shape:
            msg = ('Coordinate inputs must have same shape. ',
                   'Got x-shape of {} and y-shape of {}.')
            raise ValueError(msg.format(x_coord.shape, y_coord.shape))
# NOTE: would like to check that dims are in correct order, but can't do that
# if there is no cube.
# TODO: **document** -- another input format requirement
#        x_dims, y_dims = (cube.coord_dims(co) for co in (x_coord, y_coord))
#        if x_dims != (0, 1) or y_dims != (0, 1):
#            msg = ('Coordinate inputs must map to cube dimensions (0, 1). ',
#                   'Got x-dims of {} and y-dims of {}.')
#            raise ValueError(msg.format(x_dims, y_dims))
        if x_coord.has_bounds() and y_coord.has_bounds():
            x, y = x_coord.bounds, y_coord.bounds
        else:
            x, y = x_coord.points, y_coord.points

    elif isinstance(x, iris.coords.Coord) or isinstance(y, iris.coords.Coord):
        is_and_not = ('x', 'y')
        if isinstance(y, iris.coords.Coord):
            is_and_not = reversed(is_and_not)
        msg = 'Input {!r} is a Coordinate, but {!r} is not.'
        raise ValueError(*is_and_not)

    # Now have either 2 points arrays or 2 bounds arrays.
    # Construct (lhs, mid, rhs) where these represent 3 adjacent points with
    # increasing longitudes.
    if x.ndim == 2:
        # PROBLEM: we can't use this if data is not full-longitudes,
        # i.e. rhs of array must connect to lhs (aka 'circular' coordinate).
        # But we have no means of checking that ?

        # Use previous + subsequent points along longitude-axis as references.
        # NOTE: we also have no way to check that dim #2 really is the 'X' dim.
        mid = np.array([x, y])
        lhs = np.roll(mid, 1, 2)
        rhs = np.roll(mid, -1, 2)
        if not x_coord:
            # Create coords for result cube : with no bounds.
            y_coord = iris.coords.AuxCoord(x, standard_name='latitude',
                                           units='degrees')
            x_coord = iris.coords.AuxCoord(y, standard_name='longitude',
                                           units='degrees')
    else:
        # Get lhs and rhs locations by averaging top+bottom each side.
        # NOTE: so with bounds, we *don't* need full circular longitudes.
        xyz = _3d_xyz_from_latlon(x, y)
        angle_boundpoints_vals = {'mid-lhs, mid-rhs': '03_to_12',
                                  'lower-left, lower-right': '0_to_1'}
        bounds_pos = angle_boundpoints_vals.get(cell_angle_boundpoints)
        if bounds_pos == '0_to_1':
            lhs_xyz = xyz[..., 0]
            rhs_xyz = xyz[..., 1]
        elif bounds_pos == '03_to_12':
            lhs_xyz = 0.5 * (xyz[..., 0] + xyz[..., 3])
            rhs_xyz = 0.5 * (xyz[..., 1] + xyz[..., 2])
        else:
            msg = ('unrecognised cell_angle_boundpoints of "{}", '
                   'must be one of {}')
            raise ValueError(msg.format(cell_angle_boundpoints,
                                        list(angle_boundpoints_vals.keys())))
        if not x_coord:
            # Create bounded coords for result cube.
            # Use average lhs+rhs points in 3d to get 'mid' points, as coords
            # with no points are not allowed.
            mid_xyz = 0.5 * (lhs_xyz + rhs_xyz)
            mid_latlons = _latlon_from_xyz(mid_xyz)
            # Create coords with given bounds, and averaged centrepoints.
            x_coord = iris.coords.AuxCoord(
                points=mid_latlons[0], bounds=x,
                standard_name='longitude', units='degrees')
            y_coord = iris.coords.AuxCoord(
                points=mid_latlons[1], bounds=y,
                standard_name='latitude', units='degrees')
        # Convert lhs and rhs points back to latlon form -- IN DEGREES !
        lhs = np.rad2deg(_latlon_from_xyz(lhs_xyz))
        rhs = np.rad2deg(_latlon_from_xyz(rhs_xyz))
        # mid is coord.points, whether input or made up.
        mid = np.array([x_coord.points, y_coord.points])

    # Do the angle calcs, and return as a suitable cube.
    angles = _angle(lhs, mid, rhs)
    result = iris.cube.Cube(angles,
                            long_name='gridcell_angle_from_true_east',
                            units='radians')
    result.add_aux_coord(x_coord, (0, 1))
    result.add_aux_coord(y_coord, (0, 1))
    return result


def true_vectors_from_grid_vectors(u_cube, v_cube,
                                   grid_angles_cube=None,
                                   grid_angles_kwargs=None):
    """
    Rotate distance vectors from grid-oriented to true-latlon-oriented.

    Args:

    * u_cube, v_cube : (cube)
        Cubes of grid-u and grid-v vector components.
        Units should be differentials of true-distance, e.g. 'm/s'.

    Optional args:

    * grid_angles_cube : (cube)
        gridcell orientation angles.
        Units must be angular, i.e. can be converted to 'radians'.
        If not provided, grid angles are estimated from 'u_cube' using the
        :func:`gridcell_angles` method.

    * grid_angles_kwargs : (dict or None)
        Additional keyword args to be passed to the :func:`gridcell_angles`
        method, if it is used.

    Returns:

        true_u, true_v : (cube)
            Cubes of true-north oriented vector components.
            Units are same as inputs.

    """
    u_out, v_out = (cube.copy() for cube in (u_cube, v_cube))
    if not grid_angles_cube:
        grid_angles_kwargs = grid_angles_kwargs or {}
        grid_angles_cube = gridcell_angles(u_cube, **grid_angles_kwargs)
    gridangles = grid_angles_cube.copy()
    gridangles.convert_units('radians')
    uu, vv, aa = (cube.data for cube in (u_out, v_out, gridangles))
    mags = np.sqrt(uu*uu + vv*vv)
    angs = np.arctan2(vv, uu) + aa
    uu, vv = mags * np.cos(angs), mags * np.sin(angs)

    # Promote all to masked arrays, and also apply mask at bad (NaN) angles.
    mask = np.isnan(aa)
    for cube in (u_out, v_out, aa):
        if hasattr(cube.data, 'mask'):
            mask |= cube.data.mask
    u_out.data = np.ma.masked_array(uu, mask=mask)
    v_out.data = np.ma.masked_array(vv, mask=mask)

    return u_out, v_out