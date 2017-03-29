dbuild - a parallel docker build manager
========================================

dbuild makes it simple to build and manage lots of Docker containers at
once. It can be used to simplify development workflows, or to manage builds in
CI/CD pipelines.

Features
--------

 * Builds and pushes many Dockerfiles at once
 * Runs builds in parallel
 * Extremely lenient CLI
 * Fancy progress bars

Requirements
------------

 * Docker 1.13 or higher
 * Python 2.7 and pip

Install
-------

```
pip install git+https://github.com/timothyb89/dbuild.git
```

Quickstart Examples
----------

Build `latest` tag in many modules:
```
dbuild build module-a module-b module-c latest
```

Build and push all variants of a module:
```
dbuild build push module-a all
```

Build a module's `latest`, but override a build arg and push under a new tag:
```
dbuild build push module-a latest BRANCH=refs/changes/29/427329/13 :testing
```

Build and push lots of modules in parallel:
```
dbuild build push module-a module-b module-c latest --workers=3
```

Build and push a testing image to your personal repository, using the `latest`
config as a template but overriding a build arg:
```
dbuild build push module-a latest my-user/ BRANCH=testing
```

Like above, but build several modules:
```
dbuild build push module-a module-b module-c my-user/
```

Build and push the `latest` variant of lots of modules to both their configured
repositories and to an additional registry, preserving all namespaces, image
names, and tags defined in the `latest` variant:
```
dbuild build push module-a module-b module-c latest + my-registry.private.com:1234
```

Detailed Usage
--------------

dbuild works inside a module directory. A module directory has many
subdirectories that contain Dockerfiles, and the directory names are
used as module names.

To run a build, pass `dbuild` a list of verbs and a list of modules:

```bash
dbuild build module-a module-b module-c
```

Valid verbs include:
 * `build` - builds docker images
 * `push` - pushes docker images to their registry

Verbs form a pipeline, so while `push` will examine other arguments to
determine what images to push, `build push` explicitly pushes the result
of `build`.

### `build.yml`

While dbuild can build one module at a time with no configuration at
all, many extra features become available when a `build.yml` is provided
in each module directory.

A valid `build.yml` looks like this:
```
repository: reponame/some-image
variants:
  - tag: master
    args:
      IMAGE_BRANCH: master
  - tag: 1.0.0
    aliases:
      - :1.0
      - :1
      - :latest
    args:
      IMAGE_BRANCH: master
```

In these files, you can specify the target repository for the built
image as well as a number of image variants. When building, the special `all`
variant can be used to build every variant defined in `build.yml`.

### Variants and tags

Several verbs (e.g. `build`, `push`) operate on variants and tags. "Tag" in
this case refers to a full Docker image "link", and includes four components:

 * **registry**: the docker registry where this image is located, this field is
   optional and refers to Docker Hub when not specified
 * **namespace**: the user or group name the image belongs to. This is required
   for images on Docker Hub, but may be unset for a private registry
 * **image name**: the name of the image. When combined with a namespace (and
   optionally a registry), this may be referred to as the image's **repository**
 * **tag**: the image tag, often referring to an image version. While Docker's
   CLI allows the tag to be omitted (defaults to `:latest`), dbuild requires a
   tag to be specified in all cases (yes, there is some overloading of the term
   'tag')

A full tag may look like this:

```
registry.example.com:1234/namespace/image:tag
```

dbuild can operate simultaneously on many tags that all refer to the same
actual image. This can come in handy when building an image if you want to
push the result to multiple registries, handle version aliases (e.g. `latest`),
or push an image to a different location than defined in your `build.yml`
(if you want to distribute a testing image, for instance).

If a `build.yml` is available, dbuild will use it to generate a "base tag" for
whichever variant you're trying to build (if any). This can have a few
different results:

 * If a variant is specified, the base tag will include the **namespace**,
   the **image name**, and optionally a **registry** from a `repository` field
   either within the variant block or from the top level. The **tag** will also
   be set using the variant's `tag` field.
 * If no variant is specified, the base tag will include the top-level
   `repository` field, potentially setting the **namespace**, **image name**,
   and **registry**
 * If no `build.yml` exists, the base tag is empty and all fields must be set
   via arguments on the command line

Variants and tags can be overridden, modified, or added to using command-line
arguments, allowing images to be conditionally pushed anywhere - even to
multiple registries - easily. To do so, pass an ordered list of the following
tag arguments in any position:

 * `:tag`
 * `/image`
 * `/image:tag`
 * `namespace/`
 * `namespace/image`
 * `namespace/image:tag` (note, this will *unset* the **registry** field, think
   of it as including an implicit reference to Docker Hub)
 * `registry.example.com:1234`
 * `registry.example.com:1234/namespace`
 * `registry.example.com:1234/namespace/image`
 * `registry.example.com:1234/namespace/image:tag`
 * `+`: append to, rather than replace, the tags generated from `repository`,
   `tag` and `aliases` fields in `build.yml`

Replace `namespace`, `image`, `tag`, and `registry` with real values as
desired, the syntax is what matters. The first tag argument will mutate
the base tag. Each subsequent tag argument will mutate the result of the
preceding tag argument.

Tag arguments apply to all modules and all variants. This means special care
must be taken to avoid generating conflicting tag names, but allows for a lot
of flexibility. See the Quickstart Examples above for some ideas on usage.

To help debug tag syntax, you can take advatage of the `resolve` verb. This
verb simply prints each module and a list of variants and tags that will be
generated based on your command-line arguments.

Note that the `aliases` field in `build.yml` uses the exact same rules and
syntax (except `+` is ignored).

### Verb: `build`

The `build` verb manages `docker build ...` and supports a number of argument
types:
 * Variant: one or more variants from `build.yml` to build
 * Repository: set or override repository from `build.yml`
 * Any set tag/variant resolvers as described above
 * `key=value`: add or override a build arg (`key=` to unset)
 * `@rebuild`: one or more rebuild targets, see below

Note that arguments apply to all modules. In other words, if you build several
modules, any tags, build args, etc will apply to all provided modules. Any
module-specific parameters should be defined in `build.yml` or modules should
be built separately.

If no `build.yml` exists, a full image repository needs to be specified, like
`myrepo/myimage:latest`. A full example of a config-less build might look like:

```
dbuild build push some-module myrepo/myimage
```

Note that as there is no `build.yml`, no variants can be specified on the
command-line. As it wouldn't make sense to build many modules with the exact
same full image/repo name, you can only build one config-less module at a time.
Build arguments, `@rebuild` targets, and `::extra` tags can all be specified
without any trouble.

### Verb: `push`

The `push` verb will push images to their associated Docker registries. It
doesn't alter vanilla Docker behavior, so if the image repository contains a
registry hostname, it will push to that instead of the default Docker Hub.

### Rebuilds and Caching

dbuild helps reduce image rebuild time by taking advantage of Docker layers as
well as the new (experimental) `--squash` functionality. By logically splitting
up `RUN` instructions in Dockerfiles it is possible to only rebuild certain
parts of a Dockerfile and skip unnecessary or time consuming steps like
dependency installation.

As an example, this take [this compact Dockerfile][1]:
```
RUN apk add --no-cache python py2-pip py2-jinja2 && \
  apk add --no-cache --virtual build-dep \
     python-dev git make g++ linux-headers && \
  git clone \
    --single-branch --depth=1 -b $PERSISTER_BRANCH \
    $PERSISTER_REPO /monasca-persister && \
  pip install influxdb && \
  cd /monasca-persister && \
  pip install -r requirements.txt && \
  python setup.py install && \
  cd / && \
  rm -rf /monasca-persister && \
  apk del build-dep
```

This can be rewritten as follows:
```
ARG REBUILD_DEPENDENCIES=1
RUN apk add --no-cache python py2-pip py2-jinja2 && \
  apk add --no-cache --virtual build-dep \
     python-dev git make g++ linux-headers

ARG REBUILD_CHECKOUT=1
RUN git clone \
    --single-branch --depth=1 -b $PERSISTER_BRANCH \
    $PERSISTER_REPO /monasca-persister && \
  pip install influxdb && \
  cd /monasca-persister && \
  pip install -r requirements.txt && \
  python setup.py install && \
  cd / && \
  rm -rf /monasca-persister && \
  apk del build-dep
```

Now, when running `build`, dbuild scans the Dockerfile and locates the `REBUILD`
instructions. On the dbuild command line, these can be set with:

```
dbuild build monasca-persister-python latest @checkout
```

dbuild will then generate a unique build argument for `REBUILD_CHECKOUT`,
forcing `docker build` to rebuild from that point in the Dockerfile.

### Other options

* `-d`, `--debug`: turn on debug logging
* `-s`, `--show-plans`: display the planning tree before running
* `-w`, `--workers`: set the number of worker threads (1 by default)

[1]: https://github.com/hpcloud-mon/monasca-docker/blob/9d33f282fa80caba30c8a0259a64b7f01ba0f0e4/monasca-persister-python/Dockerfile#L26
