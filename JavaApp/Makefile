# Prerequisite variables
SOURCEDIR := src
OUTPUTDIR := build

SOURCES := $(shell find $(SOURCEDIR) -type f -name '*.java')
CLASSES := $(SOURCES:$(SOURCEDIR)/%.java=$(OUTPUTDIR)/%.class)

TARGETS := $(shell find $(SOURCEDIR) -mindepth 1 -maxdepth 1 -type d)
TARGETS := $(TARGETS:$(SOURCEDIR)/%=$(OUTPUTDIR)/%.jar)
# [ZSJ-patch] Also build the legacy LWJGL 3.3.3 stack jar (1.21.x runtime),
#   into build/libs_legacy/ (kept out of the main libs/ classpath wildcard).
TARGETS += $(OUTPUTDIR)/libs_legacy/lwjgl-legacy.jar

# Add all jars to classpath and allow compiling with private API
# [ZSJ-patch] Exclude libs/lwjgl_legacy/*.jar from the COMPILE classpath.
#   lwjgl_legacy holds the LWJGL 3.3.3 jars for the 1.21.x runtime stack; they
#   must NOT be mixed with the 3.4.1 jars at compile time (duplicate classes).
#   They are still packaged into lwjgl-legacy.jar by the dedicated rule below.
LEGACY_JARS := $(wildcard libs/lwjgl_legacy/*.jar)
COMPILE_JARS := $(filter-out $(LEGACY_JARS),$(wildcard libs/*/*.jar))
JCFLAGS := -cp "$(subst $() $(),:,$(wildcard $(SOURCEDIR)/*/):$(COMPILE_JARS))" -XDignore.symbol.file

all: $(TARGETS)
	
# Sometimes the CI produces empty class file resulting in broken jar, so we check and abort here
check_empty_classes:
	@set -e
	@echo "Checking for empty class files"
	$(eval EMPTY_CLASSES := $(shell find $(OUTPUTDIR) -name '*.class' -size 0))
	@if [ -n "$(EMPTY_CLASSES)" ]; then \
		echo "Error: found empty class files in $@: $(EMPTY_CLASSES)"; \
		exit 1; \
	fi

# Rules for building java class
$(CLASSES): $(OUTPUTDIR)/%.class: $(SOURCEDIR)/%.java
	@set -e
	$(eval TARGET=$(word 2, $(subst /, ,$@)))
	@mkdir -p $(OUTPUTDIR)/classes
	@mkdir -p $(dir $@)
	@echo "Compiling $<"
	@$(BOOTJDK)/javac $(JCFLAGS) -d $(OUTPUTDIR)/classes $<
	@cp $(@:$(OUTPUTDIR)/$(TARGET)/%.class=$(OUTPUTDIR)/classes/%*.class) $(dir $@)/

$(OUTPUTDIR)/launcher.jar: $(CLASSES) check_empty_classes
	@set -e
	@echo "Creating $@"
	@$(BOOTJDK)/jar -cf $@ -C $(basename $@) .

$(OUTPUTDIR)/lwjgl.jar: $(CLASSES) check_empty_classes
	@set -e
	@echo "Creating $@ (LWJGL 3.4.1 main stack)"
	@mkdir -p $(OUTPUTDIR)/lwjgl_lib
	@cd $(OUTPUTDIR)/lwjgl_lib && find ../../libs/lwjgl -maxdepth 1 -name 'lwjgl*.jar' -exec $(BOOTJDK)/jar -xf {} \; && rm -rf META-INF
	@cp -R $(OUTPUTDIR)/lwjgl/* $(OUTPUTDIR)/lwjgl_lib/
	@$(BOOTJDK)/jar -cf $@ -C $(OUTPUTDIR)/lwjgl_lib .

# [ZSJ-patch] Legacy LWJGL 3.3.3 stack for Minecraft <= 1.21.x (see JavaLauncher.m
#   stack selection). Built into build/libs_legacy/ so it does NOT share the app
#   libs/ classpath wildcard with lwjgl.jar (3.4.1) -- both would otherwise be
#   loaded together and clash. JavaLauncher.m appends libs_legacy/* to -cp only
#   when the legacy stack is selected.
$(OUTPUTDIR)/libs_legacy/lwjgl-legacy.jar: $(CLASSES) check_empty_classes
	@set -e
	@echo "Creating $@ (LWJGL 3.3.3 legacy stack)"
	@mkdir -p $(OUTPUTDIR)/lwjgl_legacy_lib
	@cd $(OUTPUTDIR)/lwjgl_legacy_lib && find ../../libs/lwjgl_legacy -maxdepth 1 -name 'lwjgl*.jar' -exec $(BOOTJDK)/jar -xf {} \; && rm -rf META-INF
	@mkdir -p $(dir $@)
	@$(BOOTJDK)/jar -cf $@ -C $(OUTPUTDIR)/lwjgl_legacy_lib .

$(OUTPUTDIR)/patchjna_agent.jar: $(CLASSES)
	@set -e
	@mv $(basename $@)/com/sun/jna/Platform.class $(basename $@)/com/sun/jna/Platform.class.patch
	@echo "Creating $@"
	@$(BOOTJDK)/jar -cfm $@ $(SOURCEDIR)/patchjna_agent.txt -C $(basename $@) .

clean:
	rm -rf $(OUTPUTDIR)

.SUFFIXES: .java
.PHONY: all clean
